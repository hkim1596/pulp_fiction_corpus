import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("site_refresh", Path(__file__).resolve().parents[1] / "scripts/site_refresh.py")
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


class RefreshRetry(unittest.TestCase):
    def test_failed_build_retries_after_exports_stop_changing(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "data/corpus").mkdir(parents=True)
            publication = types.SimpleNamespace(POOL=str(Path(root, "pool")),
                publish_all=lambda: {"published": 0}, write_pool=lambda: 0)
            export = types.SimpleNamespace(export_corpus=lambda **kw: 0, export_pilot_live=lambda **kw: 0)
            failed = types.SimpleNamespace(returncode=1, stdout="", stderr="build failed")
            success = types.SimpleNamespace(returncode=0, stdout='{"counts":{"stories":42}}', stderr="")
            with patch.object(refresh, "ROOT", root), patch.dict("sys.modules", {"s13_publish": publication, "r00_export_stories": export}), \
                 patch("corpus_lib.all_states", return_value={}), patch("corpus_lib.log"), \
                 patch.object(refresh.subprocess, "run", side_effect=[failed, success]) as run:
                self.assertFalse(refresh.cycle(first=True)["build_ok"])
                self.assertTrue(Path(root, "data/corpus/site_build_pending.json").exists())
                self.assertTrue(refresh.cycle()["build_ok"])
                self.assertFalse(Path(root, "data/corpus/site_build_pending.json").exists())
                self.assertEqual(refresh.cycle()["stories"], 42)
                self.assertEqual(run.call_count, 2)

    def test_timeout_persists_retry_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "data/corpus").mkdir(parents=True)
            publication = types.SimpleNamespace(POOL=str(Path(root, "pool")), publish_all=lambda: {"published": 0}, write_pool=lambda: 0)
            export = types.SimpleNamespace(export_corpus=lambda **kw: 0, export_pilot_live=lambda **kw: 0)
            with patch.object(refresh, "ROOT", root), patch.dict("sys.modules", {"s13_publish": publication, "r00_export_stories": export}), \
                 patch("corpus_lib.all_states", return_value={}), patch("corpus_lib.log"), \
                 patch.object(refresh.subprocess, "run", side_effect=subprocess.TimeoutExpired("build", 1)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    refresh.cycle(first=True)
            self.assertTrue(Path(root, "data/corpus/site_build_pending.json").exists())


if __name__ == "__main__":
    unittest.main()
