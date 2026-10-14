"""Selection must catch hidden analysis changes without modifying corpus inputs."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_continuity_recheck as planner


def analysis(**start):
    return {"offset": 0, "pages": {1: {"starts": [dict(title="Story", author="Writer",
             title_idx=[0], byline_idx=1, subtitle_idx=[], **start)], "cont_from": [], "furniture": []}}}


class RecheckPlanTests(unittest.TestCase):
    def test_absent_facing_title_and_none_do_not_select_every_issue(self):
        old = analysis()
        new = copy.deepcopy(old)
        new["pages"][1]["starts"][0]["facing_title"] = None
        old["scan_of"] = lambda page: page
        new["scan_of"] = lambda page: page + 1
        old["vocab"], new["vocab"] = {"body": 1}, {"body": 2}
        result = planner.analysis_difference(old, new)
        self.assertFalse(result["selected"])

    def test_hidden_subtitle_change_is_detected_without_persisted_start_change(self):
        old = analysis()
        new = copy.deepcopy(old)
        new["pages"][1]["starts"][0]["subtitle_idx"] = [2]
        result = planner.analysis_difference(old, new)
        self.assertTrue(result["selected"])
        self.assertEqual(result["changed_pages"], ["1"])
        self.assertEqual(result["reasons"], ["analysis_changed"])

    def test_standalone_continued_from_selects_assembly_only_change(self):
        old = analysis()
        old["pages"][1]["cont_from"] = [4]
        result = planner.analysis_difference(old, copy.deepcopy(old))
        self.assertTrue(result["selected"])
        self.assertEqual(result["reasons"], ["continued_from_assembly_recheck"])
        self.assertFalse(result["changed_pages"])

    def test_held_issue_is_selected_even_with_unchanged_analysis(self):
        result = planner.analysis_difference(analysis(), analysis(), held=True)
        self.assertEqual(result["reasons"], ["previous_hold"])
        self.assertTrue(result["selected"])

    def test_actual_isolated_modules_compare_same_layout_and_write_nothing(self):
        with tempfile.TemporaryDirectory(prefix="recheck-plan-") as temporary:
            root = Path(temporary)
            layout = root / "data/layout/fixture"
            layout.mkdir(parents=True)
            (layout / "page_0001.json").write_text(json.dumps({"page": 1, "regions": []}))
            paths = [root / "before.py", root / "after.py"]
            old, new = analysis(), analysis()
            new["pages"][1]["starts"][0]["subtitle_idx"] = [2]
            for path, document in zip(paths, [old, new]):
                path.write_text("import json\nfrom pathlib import Path\nROOT = None\n"
                                "def load_pages(iid):\n"
                                "    return {1: json.loads((Path(ROOT)/'data/layout'/iid/'page_0001.json').read_text())}\n"
                                "def analyse(pages):\n"
                                "    assert pages[1]['page'] == 1\n"
                                f"    return {document!r}\n")
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = planner.compare_issue(str(root), {"id": "fixture"}, str(paths[0]), str(paths[1]))
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["reasons"], ["analysis_changed"])
            self.assertEqual(result["pages"], 1)
            self.assertEqual(before, after)

    def test_missing_layout_is_conservatively_selected(self):
        with tempfile.TemporaryDirectory(prefix="recheck-plan-") as temporary:
            path = Path(temporary) / "rules.py"
            path.write_text("ROOT = None\n")
            result = planner.compare_issue(temporary, {"id": "missing"}, str(path), str(path))
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["selected"])
            self.assertIn("no layout pages", result["error"])

    def test_inventory_includes_newly_assembled_and_failed_receipts(self):
        with tempfile.TemporaryDirectory(prefix="recheck-plan-") as temporary:
            root = Path(temporary)
            state = root / "data/corpus/state"
            state.mkdir(parents=True)
            issues = [{"id": name} for name in ("old", "new", "held", "not_ready")]
            (state / "old.json").write_text('{"stages":{"assembled":{}}}')
            first, _ = planner.inventory(root, issues)
            self.assertEqual([x["id"] for x in first], ["old"])
            (state / "new.json").write_text('{"stages":{"assembled":{}}}')
            receipt = root / "data/corpus/continuity-2026-10-07/held/receipt.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text('{"status":"failed"}')
            second, held = planner.inventory(root, issues)
            self.assertEqual([x["id"] for x in second], ["old", "new", "held"])
            self.assertEqual(held, {"held"})


if __name__ == "__main__":
    unittest.main()
