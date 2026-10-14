"""Guarded migration integration tests on disposable fixtures, including symlinked scan storage."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import apply_issue_id_migration as apply_migration
import plan_issue_id_migration as planner


class MigrationApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pulp-migration-apply-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "project"
        (self.root / "config").mkdir(parents=True)
        self.issues = [{"id": "shared", "ia_identifier": source, "magazine": "Argosy", "cover_date": "1923-12"}
                       for source in ("Argosy_v156n02_1923-12-01.Munsey_ufikus-DPP", "Argosy_v156n03_1923-12-08.Munsey_ufikus-DPP")]
        self.issues.append({"id": "safe", "ia_identifier": "safe-source", "magazine": "Other", "cover_date": "1924-01"})
        self.write("config/corpus_issues.json", {"issues": self.issues})
        self.write("config/corpus_approval.json", {"approved": True, "approved_by": "Original approver",
                                                   "list_sha256": planner.fingerprint(self.issues)})
        for stop in ("STOP", "STOP_LLM", "STOP_SITE"):
            path = self.root / "data/corpus" / stop
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        for category in ("pages", "thumbs"):
            physical = self.base / f"physical-{category}"
            (physical / "shared").mkdir(parents=True)
            (physical / "shared/page_0001.jpg").write_bytes(b"original scan bytes")
            (self.root / "data" / category).symlink_to(physical, target_is_directory=True)
        self.write("data/raw/shared/meta.json", {"metadata": {"identifier": self.issues[0]["ia_identifier"]}})
        self.write("data/corpus/state/shared.json", {"stages": {"downloaded": {"ia_identifier": self.issues[0]["ia_identifier"]}}})
        self.write("data/export/corpus/shared.jsonl", {"issue": "shared", "text": "original exported story"})
        self.write("data/work_surya/shared/ocr/results.json", {"page_0001": []})
        self.write("data/articles/shared/articles.json", {"articles": []})
        self.write("data/raw/safe/meta.json", {"unaffected": True})
        self.report, proposed, original, approval = planner.plan(self.root)
        self.proposal = self.base / "proposal"
        self.proposal.mkdir()
        for name, value in (("plan.json", self.report), ("corpus_issues.proposed.json", proposed),
                            ("corpus_issues.original.json", original), ("corpus_approval.original.json", approval),
                            ("corpus_approval.proposed.json", planner.migrated_approval(self.report, approval))):
            (self.proposal / name).write_text(json.dumps(value), encoding="utf-8")
        self.archive = self.root / "data/identity_quarantine" / self.report["migration_id"]
        self.writer_patch = patch.object(apply_migration, "active_writers", return_value=[])
        self.writer_patch.start()
        self.addCleanup(self.writer_patch.stop)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_apply_archives_real_scan_targets_and_preserves_every_file(self):
        original_approval_bytes = (self.root / "config/corpus_approval.json").read_bytes()
        result = apply_migration.apply(self.root, self.proposal)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["sources_to_reprocess"], 2)
        self.assertEqual(result["unaffected_entries"], 1)
        current = apply_migration.read_json(self.root / "config/corpus_issues.json")
        approval = apply_migration.read_json(self.root / "config/corpus_approval.json")
        self.assertEqual(len({i["id"] for i in current["issues"]}), 3)
        self.assertEqual(approval["list_sha256"], planner.fingerprint(current["issues"]))
        self.assertEqual(planner.membership(current["issues"]), planner.membership(self.issues))
        self.assertEqual((self.archive / "snapshots/config/corpus_approval.json").read_bytes(), original_approval_bytes)
        self.assertFalse((self.base / "physical-pages/shared").exists())
        self.assertFalse((self.base / "physical-thumbs/shared").exists())
        self.assertTrue((self.root / "data/pages").is_symlink())
        self.assertFalse((self.root / "data/export/corpus/shared.jsonl").exists())
        self.assertTrue((self.root / "data/raw/safe/meta.json").exists())
        journal = apply_migration.read_json(self.archive / "journal.json")
        for artifact in journal["artifacts"]:
            self.assertEqual(artifact["status"], "archived")
            target = self.root / artifact["archive"]
            for entry in artifact["entries"]:
                if entry["kind"] == "file":
                    file = target if entry["relative"] == "." else target / entry["relative"]
                    self.assertEqual(apply_migration.digest(file), entry["sha256"])
        self.assertTrue((self.root / "data/corpus/STOP").exists())

    def test_changed_approval_stops_before_archiving(self):
        self.write("config/corpus_approval.json", {"approved": False})
        with self.assertRaisesRegex(RuntimeError, "changed after"):
            apply_migration.apply(self.root, self.proposal)
        self.assertFalse(self.archive.exists())
        self.assertTrue((self.root / "data/export/corpus/shared.jsonl").exists())

    def test_new_human_correction_stops_before_archiving(self):
        self.write("data/annotations/shared.jsonl", {"action": "verify"})
        with self.assertRaisesRegex(RuntimeError, "human annotations"):
            apply_migration.apply(self.root, self.proposal)
        self.assertFalse(self.archive.exists())

    def test_active_writer_stops_before_archiving(self):
        with patch.object(apply_migration, "active_writers", return_value=[{"pid": 123, "scripts": ["run_corpus.py"]}]):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                apply_migration.apply(self.root, self.proposal)
        self.assertFalse(self.archive.exists())

    def test_new_destination_stops_before_archiving(self):
        target = self.report["groups"][0]["sources"][0]["new_id"]
        (self.root / "data/pages" / target).mkdir()
        with self.assertRaisesRegex(RuntimeError, "destination already exists"):
            apply_migration.apply(self.root, self.proposal)
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
