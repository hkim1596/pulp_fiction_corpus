"""scripts/paratext_audit.py (11 November 2026): the paratext records of an assembly by rule and family; an R-B record is
confirmed when one of its boxes is in the issue's recurrence evidence; the unconfirmed ones of 40 words or more are listed."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paratext_audit as PA


class ParatextAudit(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(PA.selftest())

    def test_split_from_as_list_and_missing_fields(self):
        doc = {"articles": [{"article_id": "y_a001", "type": "feature", "text": "t", "fragments": [{"page": 2, "region_ids": [0]}]},
                            {"article_id": "y_a009", "type": "house", "paratext": "masthead", "split_from": ["y_a001"], "text": "Copyright 1914",
                             "fragments": [{"page": 2, "region_ids": [1]}]},
                            {"article_id": "y_a010", "type": "ad", "paratext": "advertisement"}]}
        rows = {r["id"]: r for r in PA.audit_doc(doc, {}, "y", "Y", "book")}
        self.assertEqual(rows["y_a009"]["from"], "feature")
        self.assertEqual(rows["y_a009"]["around"], ["feature"])
        self.assertFalse(rows["y_a009"]["confirmed"])
        self.assertEqual((rows["y_a010"]["words"], rows["y_a010"]["page"], rows["y_a010"]["around"]), (0, None, []))

    def test_magazine_name(self):
        self.assertEqual(PA.magazine_of("true_detective_mysteries_1929_12_cember1929"), "true detective mysteries")
        self.assertEqual(PA.magazine_of("x_undated_abc"), "x")
        self.assertEqual(PA.magazine_of("x_1930_01", {"magazine": "X Stories"}), "X Stories")

    def test_one_reads_files(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "data", "assembly_v2", "rules", "z"))
            os.makedirs(os.path.join(d, "data", "corpus", "p52-2026-10-16", "box_recurrence"))
            json.dump({"backend": "rules_v2.6", "paratext_postprocess": {"rules": "2.14"},
                       "articles": [{"article_id": "z_a002", "type": "ad", "paratext": "advertisement", "text": "w " * 50,
                                     "fragments": [{"page": 3, "region_ids": [7]}]}]},
                      open(os.path.join(d, "data", "assembly_v2", "rules", "z", "articles.json"), "w"))
            json.dump({"3:7": {"n_issues": 9, "share": 0.9}}, open(os.path.join(d, "data", "corpus", "p52-2026-10-16", "box_recurrence", "z.json"), "w"))
            r = PA._one(("z", "Z", "magazine", d))
            self.assertEqual((r["state"], r["has_evidence"]), ("post-processed", True))
            self.assertTrue(r["rows"][0]["confirmed"])
            self.assertEqual(PA._one(("none", "N", "magazine", d)), {"issue": "none", "error": "no rules file"})


if __name__ == "__main__":
    unittest.main()
