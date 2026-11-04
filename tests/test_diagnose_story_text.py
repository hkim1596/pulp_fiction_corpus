"""scripts/diagnose_story_text.py (4 November 2026): the classes of a rehearsal match by its place in the two stories,
and a run end to end over a small snapshot and export directory."""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diagnose_story_text as D


class DiagnoseStoryText(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(D.selftest())

    def test_norm_excerpt(self):
        self.assertEqual(D.norm_excerpt("  Buy NOW,  here! "), "buy now here")
        self.assertEqual(len(D.norm_excerpt("a" * 500)), 120)

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            snap = os.path.join(d, "snap"); os.makedirs(os.path.join(snap, "tokens"))
            exp = os.path.join(d, "export"); os.makedirs(exp)
            stories = [("s1", "i1", 5000, "M", "1930-01"), ("s2", "i2", 5000, "M", "1930-02"), ("s3", "i3", 5000, "N", "1931-01"),
                       ("s4", "i4", 5000, "N", "1931-02"), ("s5", "i5", 5000, "N", "1931-03"), ("s6", "i6", 5000, "N", "1931-04"), ("s7", "i7", 400, "N", "1931-05")]
            with open(os.path.join(snap, "tokens", "stories.jsonl"), "w") as f:
                for sid, iid, n, mag, dt in stories:
                    f.write(json.dumps({"story_id": sid, "issue": iid, "n_tokens": n, "meta": {"magazine": mag, "cover_date": dt, "title": "T", "author": "A Novel" if sid == "s3" else "Jane Doe"}}) + "\n")
            ms = [{"a": "s1", "b": "s2", "len": 150, "a_tok": [4000, 4150], "b_tok": [10, 160], "excerpt": "recap of part one"},
                  {"a": "s1", "b": "s3", "len": 380, "a_tok": [1000, 1380], "b_tok": [10, 390], "excerpt": "whole small story"},
                  {"a": "s3", "b": "s4", "len": 60, "a_tok": [4950, 5000], "b_tok": [2000, 2060], "excerpt": "subscribe now to N magazine"},
                  {"a": "s3", "b": "s5", "len": 60, "a_tok": [4950, 5000], "b_tok": [2000, 2060], "excerpt": "subscribe now to N magazine"},
                  {"a": "s4", "b": "s6", "len": 60, "a_tok": [2000, 2060], "b_tok": [2000, 2060], "excerpt": "subscribe now to N magazine"},
                  {"a": "s5", "b": "s6", "len": 60, "a_tok": [2000, 2060], "b_tok": [2000, 2060], "excerpt": "subscribe now to N magazine"},
                  {"a": "s2", "b": "s6", "len": 60, "a_tok": [3000, 3060], "b_tok": [2000, 2060], "excerpt": "subscribe now to N magazine"},
                  {"a": "s1", "b": "s7", "len": 350, "a_tok": [100, 450], "b_tok": [20, 370], "excerpt": "whole"},
                  {"a": "s4", "b": "s5", "len": 40, "a_tok": [2000, 2040], "b_tok": [3000, 3040], "excerpt": "an ordinary phrase"}]
            with open(os.path.join(snap, "machine_k6_matches.jsonl"), "w") as f:
                for m in ms:
                    f.write(json.dumps(m) + "\n")
            with open(os.path.join(exp, "i1.jsonl"), "w") as f:
                f.write(json.dumps({"story_id": "s1", "type": "story", "work_id": "w1", "serial": {"part_label": "Part 1"}, "text": "x"}) + "\n")
                f.write(json.dumps({"story_id": "s2", "type": "story", "work_id": "w1", "serial": {"part_label": "Part 2"}, "text": "x"}) + "\n")
                f.write(json.dumps({"story_id": "ad1", "type": "ad", "text": "buy"}) + "\n")
            out = D.diagnose(snap, exp, log=lambda *a: None)
            self.assertEqual(out["counts"]["synopsis"], 1)
            self.assertEqual(out["counts"]["whole"], 1)          # s1 × s7 (350 of 400)
            self.assertEqual(out["counts"]["block"], 5)          # the subscription line in 5 stories of 5 issues
            self.assertEqual(out["counts"]["head"], 1)           # s1 × s3: at the head of the later story, no serial link
            self.assertEqual(out["counts"]["middle"], 1)         # the ordinary phrase
            self.assertEqual(len(out["blocks"]), 1)
            self.assertEqual(out["odd_authors"][0]["author"], "A Novel")
            self.assertTrue(any(r["relation"] == "same work" and r["class"] == "synopsis" for r in out["cross"]))
            md = D.report(out)
            self.assertIn("subscribe now", md)
            self.assertIn("| synopsis | 1 |", md)


if __name__ == "__main__":
    unittest.main()
