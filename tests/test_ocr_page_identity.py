"""OCR completeness and scan identity regressions; no Surya, network, or corpus data is used."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s02_layout_ocr as ocr
import run_corpus


class OCRPageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pulp-ocr-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.iid = "fixture"
        self.scans = self.root / "data" / "pages" / self.iid
        self.scans.mkdir(parents=True)
        self.layout = self.root / "data" / "layout" / self.iid
        self.text = self.root / "data" / "text" / self.iid / "routeA"
        self.work = self.root / "work"
        self.root_patch = patch.object(ocr, "ROOT", str(self.root))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def scans_for(self, *numbers):
        for n in numbers:
            (self.scans / f"page_{n:04d}.jpg").write_bytes(f"scan {n}".encode())

    def results(self, mapping, work=None):
        directory = (work or self.work) / "ocr" / "pages"
        directory.mkdir(parents=True, exist_ok=True)
        data = {key: [{"image_bbox": [0, 0, 100, 200], "text_lines": [{"text": value}]}]
                for key, value in mapping.items()}
        (directory / "results.json").write_text(json.dumps(data), encoding="utf-8")
        return work or self.work

    def complete_reading(self):
        self.scans_for(1, 2, 3)
        self.results({"page_0003.jpg": "third", "/input/page_0001.jpg": "first", "page_0002": "second"})
        return ocr.adapt(self.iid, str(self.work))

    def test_source_identity_survives_unordered_results(self):
        self.assertEqual(self.complete_reading(), 3)
        self.assertEqual(ocr.validate_outputs(self.iid), 3)
        for n, body in ((1, "first"), (2, "second"), (3, "third")):
            page = json.loads((self.layout / f"page_{n:04d}.json").read_text())
            self.assertEqual(page["page"], n)
            self.assertEqual(page["source_image"], f"page_{n:04d}.jpg")
            self.assertEqual((self.text / f"page_{n:04d}.txt").read_text(), body)

    def test_block_schema_and_blank_scan_are_both_complete_pages(self):
        self.scans_for(1, 2)
        directory = self.work / "ocr"
        directory.mkdir(parents=True)
        (directory / "results.json").write_text(json.dumps({
            "page_0001": [{"image_bbox": [0, 0, 100, 200], "blocks": [
                {"label": "Text", "html": "<p>The story continues.</p>"},
                {"label": "Picture", "text": "illustration"}]}],
            "page_0002": [{"image_bbox": [0, 0, 100, 200], "blocks": []}],
        }))
        self.assertEqual(ocr.adapt(self.iid, str(self.work)), 2)
        self.assertEqual((self.text / "page_0001.txt").read_text(), "The story continues.")
        self.assertEqual((self.text / "page_0002.txt").read_text(), "")
        self.assertEqual(ocr.validate_outputs(self.iid), 2)

    def test_missing_middle_result_never_relabels_or_publishes_later_page(self):
        self.scans_for(1, 2, 3)
        self.results({"page_0001": "first", "page_0003": "third"})
        with self.assertRaisesRegex(RuntimeError, r"missing pages \[2\]"):
            ocr.adapt(self.iid, str(self.work))
        self.assertFalse(self.layout.exists())
        self.assertFalse(self.text.exists())

    def test_bad_replacement_keeps_existing_complete_outputs(self):
        self.complete_reading()
        before = {p.name: p.read_bytes() for p in self.layout.iterdir()}
        self.results({"page_0001": "new first", "page_0003": "new third"})
        with self.assertRaisesRegex(RuntimeError, "incomplete OCR reading"):
            ocr.adapt(self.iid, str(self.work))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.layout.iterdir()})
        self.assertEqual(ocr.validate_outputs(self.iid), 3)

    def test_duplicate_results_are_not_mistaken_for_two_scans(self):
        self.scans_for(1, 2)
        self.results({"page_0001": "one", "page_0001.jpg": "duplicate"})
        with self.assertRaisesRegex(RuntimeError, "duplicate OCR results for page 1"):
            ocr.adapt(self.iid, str(self.work))

    def test_missing_source_scan_stops_before_ocr(self):
        self.scans_for(1, 3)
        with self.assertRaisesRegex(RuntimeError, r"missing source page images: \[2\]"):
            ocr.source_pages(str(self.scans))

    def test_empty_or_partial_output_is_read_again_in_a_fresh_attempt(self):
        self.scans_for(1, 2, 3)
        self.text.mkdir(parents=True)
        (self.text / "page_0001.txt").write_text("incomplete old reading")
        # A stale result must never be reused just because it sorts first in the old work directory.
        old_work = self.root / "data" / "work_surya" / self.iid
        self.results({"page_0001": "stale"}, old_work)
        attempts = []

        def fake_surya(pages_dir, work_dir):
            attempts.append(Path(work_dir))
            self.assertEqual(pages_dir, str(self.scans))
            self.assertNotEqual(Path(work_dir), old_work)
            self.results({f"page_{n:04d}": f"body {n}" for n in (1, 2, 3)}, Path(work_dir))

        with patch.object(ocr, "issues_download_complete", return_value={self.iid}), \
                patch.object(ocr, "surya_bin", return_value="fake-surya"), \
                patch.object(ocr, "stage_timer", side_effect=lambda *a, **k: contextlib.nullcontext()), \
                patch.object(ocr, "run_surya", side_effect=fake_surya), contextlib.redirect_stdout(io.StringIO()):
            ocr.run_issue(self.iid)
            ocr.run_issue(self.iid)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(ocr.validate_outputs(self.iid), 3)
        self.assertEqual((self.text / "page_0003.txt").read_text(), "body 3")

    def test_interrupted_write_requires_a_new_complete_attempt(self):
        self.complete_reading()
        self.results({f"page_{n:04d}": f"new {n}" for n in (1, 2, 3)})
        with patch.object(ocr, "_write_text_atomic", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                ocr.adapt(self.iid, str(self.work))
        self.assertFalse((self.layout / "ocr_complete.json").exists())
        with self.assertRaises(RuntimeError):
            ocr.validate_outputs(self.iid)

    def test_changed_scan_invalidates_completion(self):
        self.complete_reading()
        (self.scans / "page_0002.jpg").write_bytes(b"replacement scan has different content")
        with self.assertRaisesRegex(RuntimeError, "source images changed"):
            ocr.validate_outputs(self.iid)

    def test_source_changed_during_reading_is_not_published(self):
        self.scans_for(1)
        signature = ocr._source_signature(str(self.scans), ocr.source_pages(str(self.scans)))
        self.results({"page_0001": "old scan text"})
        (self.scans / "page_0001.jpg").write_bytes(b"different scan")
        with self.assertRaisesRegex(RuntimeError, "source images changed while OCR was running"):
            ocr.adapt(self.iid, str(self.work), expected_signature=signature)
        self.assertFalse(self.layout.exists())

    def test_legacy_complete_output_remains_reusable(self):
        self.complete_reading()
        (self.layout / "ocr_complete.json").unlink()
        for path in self.layout.glob("page_*.json"):
            page = json.loads(path.read_text())
            page.pop("source_image")
            path.write_text(json.dumps(page))
        self.assertEqual(ocr.validate_outputs(self.iid), 3)

    def test_runner_rejects_one_missing_page_even_when_ocr_exits_successfully(self):
        self.complete_reading()
        (self.text / "page_0002.txt").unlink()
        process = Mock(returncode=0)
        process.communicate.return_value = ("OCR skipped", "")
        with patch.object(run_corpus, "py_env", return_value={}), \
                patch.object(run_corpus, "settings", return_value={"reading": {"issue_timeout_s": 30}}), \
                patch.object(run_corpus, "issue_dirs", return_value={"pages": str(self.scans)}), \
                patch.object(run_corpus.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "missing.*page_0002.txt"):
                run_corpus.read_issue(self.iid, None)


if __name__ == "__main__":
    unittest.main()
