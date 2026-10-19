"""The click review of the non-magazine candidates (webapp/nonmagazine_review_pages.py, v0.25.0) and the apply step's
reading of its log (scripts/set_aside_nonmagazine.py --decisions)."""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp"), str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import nonmagazine_review_pages as NM
import set_aside_nonmagazine as sa


def item(iid, proposed, cls, score, in_sample=False, order=None, signals=("M1",)):
    return {"id": iid, "ia_identifier": iid, "magazine": iid.replace("_", " ").title(), "ia_title": iid, "cover_date": "1930", "genre": "weird",
            "pages": 100, "issues_in_magazine": 1, "in_sample": in_sample, "sample_order": order, "signals": list(signals), "evidence": {"M1": "t"},
            "points": {}, "class": cls, "score": score, "proposed": proposed}


TABLE = {"selected": 7440, "rule_version": "1.1", "written": "2026-10-19T14:55:00", "counts": {"proposed": {"EXCLUDE": 2, "LOOK": 1, "KEEP": 2}},
         "signal_key": {"M1": {"class": "book", "points": 3, "says": "a book title"}},
         "items": [item("plattner", "EXCLUDE", "book", 12, True, 1), item("wild_west", "EXCLUDE", "dime novel", 6),
                   item("jim_jam", "LOOK", "book", 3), item("weird_tales_1949", "KEEP", None, -6, True, 7), item("argosy_x", "KEEP", None, -8)]}


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        data = os.path.join(root, "data")
        os.makedirs(os.path.join(data, "corpus", "p52-2026-10-16"))
        json.dump(TABLE, open(os.path.join(data, "corpus", "p52-2026-10-16", "nonmagazine_candidates.json"), "w"))
        NM._G.clear()
        NM._CACHE.update({"table": (None, None), "log": (None, None)})
        NM.bind({"ROOT": root, "DATA": data, "esc": lambda s: str(s) if s is not None else "", "howto": lambda t: f"<details>{t}</details>",
                 "display_name": lambda u: u.title()})
        self.data = data

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_filters_and_clicks(self):
        page = NM.review_page({}, "sujin")
        self.assertIn("plattner", page); self.assertIn("wild_west", page); self.assertNotIn("jim_jam", page)   # the default view: EXCLUDE
        self.assertIn("name='decision' value='x'", page)
        self.assertIn("/thumb/plattner/page_0001.jpg", page)
        self.assertIn("sample slot #1", page)
        look = NM.review_page({"proposed": ["LOOK"]}, "sujin")
        self.assertIn("jim_jam", look); self.assertNotIn("wild_west", look)
        keep = NM.review_page({"proposed": ["KEEP"]}, "sujin")
        self.assertIn("weird_tales_1949", keep); self.assertNotIn("argosy_x", keep)     # only the sample's KEEP rows are listed
        guest = NM.review_page({}, "guest")
        self.assertIn("log in to decide", guest)
        # one click, then a bulk click on the rest of the view
        form = {"id": "plattner", "decision": "x", "note": "a novel", "back": "/review/nonmagazine?proposed=EXCLUDE"}
        self.assertEqual(NM.handle_post("sujin", lambda k, d="": form.get(k, d)), "/review/nonmagazine?proposed=EXCLUDE")
        bulk = {"bulk": "k", "ids": "plattner wild_west", "back": "/review/nonmagazine?proposed=EXCLUDE"}
        NM.handle_post("sujin", lambda k, d="": bulk.get(k, d))
        D = NM.decisions()
        self.assertEqual((D["plattner"]["decision"], D["plattner"]["note"]), ("x", "a novel"))        # the bulk click leaves decided rows alone
        self.assertEqual(D["wild_west"]["decision"], "k")
        page = NM.review_page({}, "sujin")
        self.assertIn("Decided: 2 of 4", page)
        self.assertIn("class='dx'", page); self.assertIn("class='dk'", page)
        # a second click changes the decision; a bad decision is ignored
        NM.handle_post("sujin", lambda k, d="": {"id": "wild_west", "decision": "x"}.get(k, d))
        self.assertEqual(NM.decisions()["wild_west"]["decision"], "x")
        self.assertFalse(NM.append_decision("sujin", "wild_west", "z"))
        lines = open(NM.log_path(), encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 3)                                                                # append-only: plattner, wild_west (bulk), wild_west again
        # the apply step reads the log
        exc, kept, und = sa.read_log_decisions(NM.log_path(), os.path.join(self.data, "corpus", "p52-2026-10-16", "nonmagazine_candidates.json"))
        self.assertEqual(sorted(r["id"] for r in exc), ["plattner", "wild_west"])
        self.assertEqual([r["id"] for r in und], ["jim_jam", "weird_tales_1949"])
        self.assertEqual(exc[0]["note"], "a novel")
        exc, kept, und = sa.read_log_decisions(NM.log_path(), os.path.join(self.data, "corpus", "p52-2026-10-16", "nonmagazine_candidates.json"), accept_proposed=True)
        self.assertEqual(sorted(r["id"] for r in exc), ["plattner", "wild_west"])
        NM.handle_post("sujin", lambda k, d="": {"id": "jim_jam", "decision": "?"}.get(k, d))
        exc, kept, und = sa.read_log_decisions(NM.log_path(), os.path.join(self.data, "corpus", "p52-2026-10-16", "nonmagazine_candidates.json"))
        self.assertIn("jim_jam", [r["id"] for r in und])                                               # not sure = undecided


if __name__ == "__main__":
    unittest.main()
