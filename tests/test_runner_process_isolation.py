"""Real pipe/process regressions; no model, network, or corpus writes."""
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import run_corpus


@unittest.skipUnless(os.name == "posix", "the runner uses POSIX process groups")
class RunnerProcessIsolationTests(unittest.TestCase):
    def test_idle_pool_worker_does_not_keep_completed_ocr_stdout_open(self):
        read_fd, write_fd = os.pipe()
        child = subprocess.Popen([sys.executable, "-c", "print('complete')"], stdout=write_fd)
        pool = run_corpus.process_pool(1)
        try:
            # Start the worker while the parent still has the writer: the same window in which
            # Popen and a lazy, fork-based pool raced on the server. A fork worker holds this
            # descriptor for its lifetime, even with close-on-exec set on the descriptor.
            worker_pid = pool.submit(os.getpid).result(timeout=15)
            self.assertNotEqual(worker_pid, os.getpid())
            child.wait(timeout=5)
            os.close(write_fd)
            write_fd = None
            self.assertEqual(os.read(read_fd, len(b"complete\n")), b"complete\n")
            ready, _, _ = select.select([read_fd], [], [], 1)
            self.assertTrue(ready, "idle process-pool worker retained the OCR write descriptor")
            self.assertEqual(os.read(read_fd, 1), b"")  # EOF before the worker is shut down
            self.assertEqual(pool.submit(os.getpid).result(timeout=5), worker_pid)
        finally:
            if write_fd is not None:
                os.close(write_fd)
            os.close(read_fd)
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            pool.shutdown(wait=True, cancel_futures=True)

    def test_timeout_cleanup_finishes_while_an_external_writer_stays_open(self):
        read_fd, write_fd = os.pipe()
        child = subprocess.Popen([sys.executable, "-c", "pass"], stdout=write_fd,
                                 stderr=subprocess.PIPE, text=True, start_new_session=True)
        child.stdout = os.fdopen(read_fd, "r")
        try:
            child.wait(timeout=5)
            start = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "Read timed out"):
                run_corpus.collect_reading(child, timeout=0.1, cleanup_timeout=0.1)
            self.assertLess(time.monotonic() - start, 2)
            self.assertTrue(child.stdout.closed)
            self.assertTrue(child.stderr.closed)
            # Our extra writer remains open: cleanup cannot rely on every descendant cooperating.
            os.fstat(write_fd)
        finally:
            os.close(write_fd)
            child.stdout.close()
            child.stderr.close()
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
