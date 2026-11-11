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


    def test_apply_republishes_without_taking_the_lock_twice(self):
        """With the real root, an issue without corrections is republished while its write lock is held: the locked
        body of the publisher must be called, never publish_issue (which takes the same lock and would wait for ever)."""
        import types
        with tempfile.TemporaryDirectory() as d:
            iid = "x"
            os.makedirs(os.path.join(d, "data", "assembly_v2", "rules", iid))
            rules_p = os.path.join(d, "data", "assembly_v2", "rules", iid, "articles.json")
            json.dump({"issue": iid, "backend": "rules_v2.13", "articles": []}, open(rules_p, "w"))
            calls = []
            fake = types.ModuleType("s13_publish")
            def publish_issue(*a, **k):
                raise AssertionError("publish_issue called while the write lock is held")
            fake.publish_issue = publish_issue
            fake._publish_issue_locked = lambda iid, mode, look, force=False: calls.append((iid, force)) or ("published", "rules", 1, 0)
            keep = (PP.ROOT, PP.BACKUP, PP.candidate, PP.paratext_boxes, PP.apply_doc, sys.modules.get("s13_publish"))
            PP.ROOT, PP.BACKUP = d, os.path.join(d, "data", "corpus", "paratext-test")
            PP.candidate = lambda iid, cfg, root=None: ([{"page": 1}], {"articles": []})
            PP.paratext_boxes = lambda cand: {}
            def apply_doc(doc, pages, para, iid, **k):
                doc["paratext_postprocess"] = {"rules": "2.14"}
                return {"pieces_changed": 1, "boxes_removed": 1, "words_removed": 5, "records_added": 1, "boxes_kept_protected": 0,
                        "boxes_kept_unmatched": 0, "pieces_text_set_by_hand": 0, "rebuilt_exact": 1, "rebuilt_by_paragraph": 0}
            PP.apply_doc = apply_doc
            sys.modules["s13_publish"] = fake
            try:
                row = PP.process_issue(iid, {}, apply=True, root=d)
            finally:
                PP.ROOT, PP.BACKUP, PP.candidate, PP.paratext_boxes, PP.apply_doc = keep[:5]
                if keep[5] is None:
                    sys.modules.pop("s13_publish", None)
                else:
                    sys.modules["s13_publish"] = keep[5]
            self.assertTrue(row["written"])
            self.assertEqual(row["published"], "published")
            self.assertEqual(calls, [(iid, True)])
            self.assertTrue(os.path.exists(os.path.join(d, "data", "corpus", "paratext-test", iid, "rules.before.json")))
            self.assertEqual(json.load(open(rules_p))["paratext_postprocess"], {"rules": "2.14"})

if __name__ == "__main__":
    unittest.main()
