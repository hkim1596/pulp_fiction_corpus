"""scripts/resolve_duplicates.py (D-5, 4 November 2026): the classes of the duplicate-item pairs, the keep order, and
--apply writing set-aside entries with the issue each stands for, skipping ids already set aside."""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import resolve_duplicates as RD


class ResolveDuplicates(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(RD.selftest())

    def test_apply_writes_entries_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "set_aside.json")
            json.dump({"issues": [{"id": "old", "class": "comic", "reason": "a comic"}]}, open(path, "w"))
            plan = {"decisions": [{"id": "dup1", "class": RD.CLASS_DUP, "duplicate_of": "keep1", "cls": "B", "reason": "r"},
                                  {"id": "old", "class": RD.CLASS_DUP, "duplicate_of": "keep2", "cls": "B", "reason": "r"}],
                    "to_decide": [{"issue_a": "a", "issue_b": "b", "decision": "keep_a"}, {"issue_a": "c", "issue_b": "d"}]}
            added, skipped = RD.apply(plan, "Sujin Kang", "2026-11-04", path=path)
            self.assertEqual(sorted(added), ["b", "dup1"])
            self.assertEqual(skipped, ["old"])
            e = {x["id"]: x for x in json.load(open(path))["issues"]}
            self.assertEqual(e["dup1"]["duplicate_of"], "keep1")
            self.assertEqual(e["b"]["duplicate_of"], "a")
            self.assertEqual(e["old"]["class"], "comic")
            added2, skipped2 = RD.apply(plan, "Sujin Kang", "2026-11-04", path=path)
            self.assertEqual(added2, [])


if __name__ == "__main__":
    unittest.main()
