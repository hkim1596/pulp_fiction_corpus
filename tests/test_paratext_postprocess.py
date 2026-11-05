"""scripts/paratext_postprocess.py (rules 2.14, step 2f): the paratext splits reach an assembly the continuity guard
holds — the piece's identity unchanged, the boxes owned once, the new record carrying the family and split_from."""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paratext_postprocess as PP


class ParatextPostprocess(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(PP.selftest())

    def test_annotation_refs(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.jsonl")
            with open(p, "w") as f:
                f.write(json.dumps({"ts": "t", "user": "u", "issue": "x", "action": "move_frag", "article_id": "x_a001", "frag": "3:2", "to_id": "x_a002"}) + "\n")
                f.write(json.dumps({"ts": "t", "user": "u", "issue": "x", "action": "new_article", "article_id": "x_u1", "frags": ["4:0", "4:1"]}) + "\n")
                f.write(json.dumps({"ts": "t", "user": "u", "issue": "x", "action": "set_text", "article_id": "x_a005", "text": "…"}) + "\n")
            keys, set_text = PP.annotation_refs(p)
            self.assertEqual(keys, {"3:2", "4:0", "4:1"})
            self.assertEqual(set_text, {"x_a005"})
        self.assertEqual(PP.annotation_refs(os.path.join(d, "none.jsonl")), (set(), set()))

    def test_owned_keys_and_next_number(self):
        doc = {"articles": [{"article_id": "x_a001", "fragments": [{"page": 1, "region_ids": [0, 2]}]},
                            {"article_id": "x_a007", "fragments": [{"page": 2, "region_ids": [1]}]}, {"article_id": "x_u3", "fragments": []}]}
        self.assertEqual(PP.owned_keys(doc), [(1, 0), (1, 2), (2, 1)])
        self.assertEqual(PP.next_number(doc, "x"), 8)


if __name__ == "__main__":
    unittest.main()
