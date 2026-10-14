"""Source identity regressions: no archive requests, production writes, or list migration."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from corpus_lib import partition_issue_ids
import s00b_select as selection
import s01c_fetch as fetch


class IssueIdIntegrityTests(unittest.TestCase):
    def test_conflicting_sources_are_all_held_and_identical_entries_deduplicate(self):
        issues = [{"id": "shared", "ia_identifier": "source-a"},
                  {"id": "safe", "ia_identifier": "source-c"},
                  {"id": "shared", "ia_identifier": "source-b"},
                  {"id": "safe", "ia_identifier": "source-c"}]
        before = json.dumps(issues)
        safe, conflicts = partition_issue_ids(issues)
        self.assertEqual(safe, [issues[1]])
        self.assertEqual(conflicts, {"shared": ["source-a", "source-b"]})
        self.assertEqual(json.dumps(issues), before)

    def test_three_argosy_sources_receive_distinct_stable_ids(self):
        sources = [f"Argosy_v156n0{n}_1923-12-{day}.Munsey_ufikus-DPP"
                   for n, day in ((2, "01"), (3, "08"), (4, "15"))]
        issues = [{"id": selection.issue_id("Argosy", "1923-12", source), "ia_identifier": source}
                  for source in sources]
        self.assertEqual(len({issue["id"] for issue in issues}), 1)  # the historical collision
        issues.append({"id": "unchanged", "ia_identifier": "other-source"})
        fixed = selection.unique_issue_ids(issues)
        self.assertEqual(len({issue["id"] for issue in fixed}), 4)
        self.assertEqual(fixed[-1]["id"], "unchanged")
        self.assertEqual({x["ia_identifier"]: x["id"] for x in fixed},
                         {x["ia_identifier"]: x["id"] for x in selection.unique_issue_ids(list(reversed(issues)))})
        self.assertEqual(partition_issue_ids(fixed)[1], {})
        self.assertEqual(len({issue["id"] for issue in issues[:3]}), 1)  # input not mutated

    def test_final_uniqueness_guard_rejects_duplicate_source_records(self):
        issue = {"id": "same", "ia_identifier": "same-source"}
        with self.assertRaisesRegex(ValueError, "still not unique"):
            selection.unique_issue_ids([issue, dict(issue)])

    def test_cached_metadata_for_another_source_is_never_reused(self):
        with tempfile.TemporaryDirectory(prefix="pulp-source-test-") as root:
            raw = Path(root) / "data" / "raw" / "shared"
            raw.mkdir(parents=True)
            metadata = raw / "meta.json"
            metadata.write_text(json.dumps({"metadata": {"identifier": "source-a"}, "files": []}))
            before = metadata.read_bytes()
            with patch.object(fetch, "get_bytes") as network:
                with self.assertRaisesRegex(RuntimeError, "cached metadata belongs to 'source-a'"):
                    fetch.fetch_issue({"id": "shared", "ia_identifier": "source-b"}, root=root)
            network.assert_not_called()
            self.assertEqual(metadata.read_bytes(), before)

    def test_cached_download_state_for_another_source_is_never_reused(self):
        with tempfile.TemporaryDirectory(prefix="pulp-source-test-") as root:
            paths = {"raw": str(Path(root) / "data" / "raw" / "shared"),
                     "master": str(Path(root) / "data" / "masters" / "shared")}
            state = {"stages": {"downloaded": {"ia_identifier": "source-a"}}}
            with patch.object(fetch, "ROOT", root), patch.object(fetch, "issue_dirs", return_value=paths), \
                    patch.object(fetch, "load_state", return_value=state), patch.object(fetch, "get_bytes") as network:
                with self.assertRaisesRegex(RuntimeError, "cached download belongs to source-a"):
                    fetch.fetch_issue({"id": "shared", "ia_identifier": "source-b"})
            network.assert_not_called()

    def test_payload_without_source_metadata_is_held(self):
        with tempfile.TemporaryDirectory(prefix="pulp-source-test-") as root:
            raw = Path(root) / "data" / "raw" / "shared"
            raw.mkdir(parents=True)
            payload = raw / "ia_text.txt"
            payload.write_text("An unidentified existing story.")
            with patch.object(fetch, "get_bytes") as network:
                with self.assertRaisesRegex(RuntimeError, "cached files have no source metadata"):
                    fetch.fetch_issue({"id": "shared", "ia_identifier": "source-b"}, root=root)
            network.assert_not_called()
            self.assertEqual(payload.read_text(), "An unidentified existing story.")

    def test_wrong_source_response_is_rejected_before_caching(self):
        with tempfile.TemporaryDirectory(prefix="pulp-source-test-") as root:
            response = json.dumps({"metadata": {"identifier": "source-a"}, "files": []}).encode()
            with patch.object(fetch, "get_bytes", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "does not identify the requested source"):
                    fetch.fetch_issue({"id": "shared", "ia_identifier": "source-b"}, root=root)
            self.assertFalse((Path(root) / "data" / "raw" / "shared" / "meta.json").exists())


if __name__ == "__main__":
    unittest.main()
