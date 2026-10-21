"""The click review of the non-magazine candidates (webapp/nonmagazine_review_pages.py, v0.25.0; kinds since 21 October
2026) and the apply step (scripts/apply_kinds.py): the log joined to the table, the migration of the earlier clicks, the
kinds and set-aside files."""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp"), str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import nonmagazine_review_pages as NM
import apply_kinds as AK


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
        self.assertIn("name='decision' value='book'", page)
        self.assertIn("/thumb/plattner/page_0001.jpg", page)
        self.assertIn("sample slot #1", page)
        self.assertIn("suggested: book", page)
        look = NM.review_page({"proposed": ["LOOK"]}, "sujin")
        self.assertIn("jim_jam", look); self.assertNotIn("wild_west", look)
        keep = NM.review_page({"proposed": ["KEEP"]}, "sujin")
        self.assertIn("weird_tales_1949", keep); self.assertNotIn("argosy_x", keep)     # only the sample's KEEP rows are listed
        guest = NM.review_page({}, "guest")
        self.assertIn("log in to decide", guest)
        # one click, then a bulk click on the rest of the view
        form = {"id": "plattner", "decision": "book", "note": "a collection", "back": "/review/nonmagazine?proposed=EXCLUDE"}
        self.assertEqual(NM.handle_post("sujin", lambda k, d="": form.get(k, d)), "/review/nonmagazine?proposed=EXCLUDE")
        bulk = {"bulk": "magazine", "ids": "plattner wild_west", "back": "/review/nonmagazine?proposed=EXCLUDE"}
        NM.handle_post("sujin", lambda k, d="": bulk.get(k, d))
        D = NM.decisions()
        self.assertEqual((D["plattner"]["decision"], D["plattner"]["note"]), ("book", "a collection"))   # the bulk click leaves decided rows alone
        self.assertEqual(D["wild_west"]["decision"], "magazine")
        page = NM.review_page({}, "sujin")
        self.assertIn("Decided: 2 of 4", page)
        self.assertIn("class='dbook'", page); self.assertIn("class='dmagazine'", page)
        # a second click changes the decision; a bad decision is ignored; an earlier exclude/keep click shows as earlier
        NM.handle_post("sujin", lambda k, d="": {"id": "wild_west", "decision": "excerpt"}.get(k, d))
        self.assertEqual(NM.decisions()["wild_west"]["decision"], "excerpt")
        self.assertFalse(NM.append_decision("sujin", "wild_west", "z"))
        with open(NM.log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-10-19T17:08:19", "user": "sujin", "id": "jim_jam", "decision": "k", "note": ""}) + "\n")
        NM._CACHE["log"] = (None, None)
        self.assertNotIn("jim_jam", NM.decisions())
        self.assertEqual(NM.decisions(with_legacy=True)["jim_jam"]["decision"], "k")
        look = NM.review_page({"proposed": ["LOOK"]}, "sujin")
        self.assertIn("earlier: keep", look)
        self.assertIn("1 earlier exclude/keep clicks still to be sorted", look)
        lines = open(NM.log_path(), encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 4)                                                                # append-only
        # the apply step reads the log joined to the table
        table = os.path.join(self.data, "corpus", "p52-2026-10-16", "nonmagazine_candidates.json")
        kinds, reasons, und, rows, D = AK.decide(table, NM.log_path())
        self.assertEqual(kinds, {"plattner": "book"})
        self.assertEqual(reasons, {"wild_west": "excerpt"})
        self.assertEqual(sorted(und), ["jim_jam", "weird_tales_1949"])
        kinds, reasons, und, rows, D = AK.decide(table, NM.log_path(), accept_proposed=True)
        self.assertEqual(sorted(und), ["weird_tales_1949"])          # jim_jam: proposed LOOK book -> book by the rule's suggestion
        self.assertEqual(kinds["jim_jam"], "book")
        # the earlier click migrated: k on a book -> book
        with open(NM.log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-10-19T17:09:00", "user": "sujin", "id": "argosy_x", "decision": "x", "note": "Amazing Serials: a partial page excerpt"}) + "\n")
        lines = AK.migrate(table_path=table, log_path=NM.log_path(), now="2026-10-21T10:00:00")
        self.assertEqual({l["id"]: l["decision"] for l in lines}, {"jim_jam": "book", "argosy_x": "excerpt"})
        self.assertEqual(AK.migrate(table_path=table, log_path=NM.log_path()), [])                     # nothing twice
        # the files
        kinds, reasons, und, rows, D = AK.decide(table, NM.log_path())
        kp = os.path.join(self.data, "corpus_kinds.json"); sp = os.path.join(self.data, "corpus_set_aside.json")
        added, changed = AK.write_kinds(kinds, rows, D, "Sujin Kang", "2026-10-21", path=kp)
        self.assertEqual((sorted(added), changed), (["jim_jam", "plattner"], []))
        kd = json.load(open(kp, encoding="utf-8"))
        self.assertEqual(kd["kinds"]["plattner"]["kind"], "book")
        self.assertEqual(kd["kinds"]["plattner"]["note"], "a collection")
        a2, skipped = AK.write_set_aside(reasons, rows, D, "Sujin Kang", "2026-10-21", path=sp, selection={"wild_west", "plattner", "jim_jam"})
        self.assertEqual([e["id"] for e in a2], ["wild_west"])
        self.assertEqual(a2[0]["class"], "excerpt of a magazine")
        a2, skipped = AK.write_set_aside(reasons, rows, D, "Sujin Kang", "2026-10-21", path=sp, selection={"wild_west"})
        self.assertEqual((a2, skipped), ([], ["wild_west"]))
        # a kind changed back to magazine leaves the file
        kinds["plattner"] = "magazine"
        added, changed = AK.write_kinds(kinds, rows, D, "Sujin Kang", "2026-10-21", path=kp)
        self.assertEqual((added, changed), ([], ["plattner"]))
        self.assertNotIn("plattner", json.load(open(kp, encoding="utf-8"))["kinds"])


if __name__ == "__main__":
    unittest.main()
