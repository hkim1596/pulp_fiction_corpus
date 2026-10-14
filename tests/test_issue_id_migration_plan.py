import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_issue_id_migration as migration


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pulp-migration-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.issues = [{"id": "shared", "ia_identifier": source, "magazine": "Argosy", "cover_date": "1923-12"}
                       for source in ("Argosy_v156n02_1923-12-01.Munsey_ufikus-DPP", "Argosy_v156n03_1923-12-08.Munsey_ufikus-DPP")]
        self.issues.append({"id": "unaffected", "ia_identifier": "another", "magazine": "Other", "cover_date": "1924-01"})
        self.config = {"issues": self.issues}
        self.approval = {"approved": True, "approved_by": "Original approver", "approved_date": "2026-10-04",
                         "list_sha256": migration.fingerprint(self.issues)}
        self.write("config/corpus_issues.json", self.config)
        self.write("config/corpus_approval.json", self.approval)

    def write(self, relative, obj):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj), encoding="utf-8")

    def test_plan_changes_only_ids_and_leaves_sources_untouched(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        report, proposed, original, approval = migration.plan(self.root)
        self.assertEqual(report["collision_groups"], 1)
        self.assertEqual(report["sources_to_reprocess"], 2)
        self.assertEqual(report["proposed_unique_ids"], 3)
        self.assertEqual(report["unaffected_entries"], 1)
        self.assertEqual(report["blockers"], [])
        self.assertEqual(migration.membership(proposed["issues"]), migration.membership(self.issues))
        self.assertEqual(original, self.config)
        self.assertEqual(approval, self.approval)
        self.assertEqual(proposed["issues"][-1]["id"], "unaffected")
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_invalid_decisions_are_reported_and_human_edits_block_automatic_rebinding(self):
        path = self.root / "data/assembly_v2/llm/shared/pages.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"page":1}\nnot json\n{"page":1}\n')
        (path.parent / "articles.json").write_text('{"articles":[')
        ann = self.root / "data/annotations/shared.jsonl"
        ann.parent.mkdir(parents=True)
        ann.write_text('{"action":"verify"}\n')
        review = self.root / "data/review/model_check.jsonl"
        review.parent.mkdir(parents=True)
        review.write_text('{"issue":"shared","decision":"agree"}\n')
        report, *_ = migration.plan(self.root)
        group = report["groups"][0]
        self.assertEqual(group["llm_articles"], "invalid")
        self.assertEqual(group["llm_decisions"]["invalid_lines"], 1)
        self.assertEqual(group["llm_decisions"]["duplicate_pages"], [1])
        self.assertEqual(group["human_model_decisions_to_preserve"], 1)
        self.assertTrue(report["blockers"])

    def test_changed_approval_is_rejected(self):
        self.write("config/corpus_approval.json", dict(self.approval, list_sha256="wrong"))
        with self.assertRaisesRegex(ValueError, "does not match"):
            migration.plan(self.root)

    def test_existing_destination_is_not_overwritten(self):
        target = migration.canonical_id(self.issues[0])
        (self.root / "data/pages" / target).mkdir(parents=True)
        report, *_ = migration.plan(self.root)
        self.assertTrue(any("destination already exists" in text for text in report["blockers"]))

    def test_approval_proposal_attributes_technical_repair_and_preserves_human_approval(self):
        report, *_ = migration.plan(self.root)
        approval = migration.migrated_approval(report, self.approval)
        self.assertEqual(approval["approved_by"], self.approval["approved_by"])
        self.assertEqual(approval["approved_date"], self.approval["approved_date"])
        self.assertEqual(approval["list_sha256"], report["proposed_list_sha256"])
        self.assertEqual(approval["identity_migrations"][-1]["performed_by"], "Codex")
        self.assertNotIn("identity_migrations", self.approval)


if __name__ == "__main__":
    unittest.main()
