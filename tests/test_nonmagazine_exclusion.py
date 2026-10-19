"""Decision 1 of 19 October 2026 (Sujin Kang): items that are not magazine issues leave corpus 1.0 and their slots in the
verification sample are redrawn. Three pieces: the candidate rule (scripts/nonmagazine_candidates.py), the apply step
(scripts/set_aside_nonmagazine.py) and the sample's --replace (scripts/draw_verification_sample.py).
"""
import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import nonmagazine_candidates as nc
import set_aside_nonmagazine as sa
import draw_verification_sample as dv


class CandidateRule(unittest.TestCase):
    def test_selftest_cases(self):
        nc.selftest()

    def test_signal_table_is_consistent(self):
        for code, (cls, pts, says) in nc.SIGNALS.items():
            self.assertTrue(cls is None or cls == "any" or cls in nc.CLASSES, code)
            self.assertTrue(says and isinstance(pts, int), code)
            if cls is None:
                self.assertIn(code, nc.AGAINST, code)

    def test_single_story_and_modern_tag(self):
        it = {"id": "x", "magazine": "Red Mask", "year": None, "undated": True}
        arch = {"title": "Red Mask 054 (paper+9fiche)-c2c", "description": "", "subjects": [], "collections": [], "date": "", "year": "",
                "files": [], "imagecount": 37, "identifier": "red-mask-054-c2c"}
        asm = {"pages": 37, "toc_n": 0, "toc_kind": None, "toc_authors": 0, "toc_chapter_titles": 0, "starts": 1, "byline_pages": 1,
               "byline_starts": 1, "chapters": 0}
        codes, ev = nc.score(it, 1, arch, asm)
        self.assertIn("M10", codes)
        self.assertIn("S4", codes)
        cls, sc, pts = nc.classify(codes)
        self.assertIn(cls, ("single story", "outside the period"))
        self.assertEqual(nc.proposal(sc), "LOOK")

    def test_a_numbered_comic_is_not_saved_by_its_number(self):
        it = {"id": "fc", "magazine": "Four Color", "year": 1943, "undated": False}
        arch = {"title": "Four Color #20 (Barney Baxter) (Dell) [1943]", "description": "", "subjects": ["comics"], "collections": ["comics_inbox"],
                "date": "1943", "year": "1943", "files": [], "imagecount": 68, "identifier": "four-color-020"}
        codes, ev = nc.score(it, 1, arch, None)
        cls, sc, pts = nc.classify(codes)
        self.assertEqual(cls, "comic")
        self.assertGreaterEqual(sc, nc.SCORE_LOOK)

    def test_review_table_lists_exclusions_looks_and_the_sample(self):
        items = [
            {"id": "a", "ia_identifier": "a", "magazine": "A", "ia_title": "A", "cover_date": "1900", "year": 1900, "genre": "western", "pages": 30,
             "issues_in_magazine": 1, "in_sample": False, "sample_order": None, "signals": ["M8", "S1"], "evidence": {"M8": "beadle"}, "points": {"dime novel": 4},
             "class": "dime novel", "score": 5, "proposed": "EXCLUDE", "archive_read": True, "assembly_read": False, "records_read": False},
            {"id": "b", "ia_identifier": "b", "magazine": "B", "ia_title": "B", "cover_date": "1930", "year": 1930, "genre": "weird", "pages": 130,
             "issues_in_magazine": 40, "in_sample": True, "sample_order": 7, "signals": ["N1", "N2"], "evidence": {}, "points": {},
             "class": None, "score": -6, "proposed": "KEEP", "archive_read": True, "assembly_read": True, "records_read": False},
            {"id": "c", "ia_identifier": "c", "magazine": "C", "ia_title": "C", "cover_date": "1940", "year": 1940, "genre": "general", "pages": 130,
             "issues_in_magazine": 3, "in_sample": False, "sample_order": None, "signals": ["M3"], "evidence": {}, "points": {},
             "class": None, "score": 1, "proposed": "KEEP", "archive_read": True, "assembly_read": True, "records_read": False},
        ]
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "out", "cand")
            counts = nc.write_outputs(items, {"selected": 3, "set_aside_already": 0}, base)
            rows = list(csv.DictReader(open(base + ".csv", encoding="utf-8")))
            self.assertEqual([r["id"] for r in rows], ["a", "b"])          # the exclusion and the sample issue; not the plain KEEP
            self.assertEqual(rows[0]["decision"], "")
            self.assertEqual(rows[1]["in_sample"], "yes")
            md = open(base + ".md", encoding="utf-8").read()
            self.assertIn("## Proposed EXCLUDE (1)", md)
            self.assertIn("## Proposed KEEP (1)", md)
            rec = json.load(open(base + ".json", encoding="utf-8"))
            self.assertEqual(rec["counts"]["proposed"], {"EXCLUDE": 1, "KEEP": 2})
            self.assertIn("M8", rec["signal_key"])


CSV = """decision,note,proposed,class,score,in_sample,sample_order,id,magazine,ia_title,cover_date,genre,pages,issues_in_magazine,signals,evidence,ia_identifier
x,a novel,EXCLUDE,book,12,yes,1,plattner,The Plattner Story,The Plattner Story And Others,1897,science fiction,320,1,M1 M2 S1,M1: title,wells-plattner
,,EXCLUDE,dime novel,5,,,wildwest,Wild West Weekly,Wild West Weekly n0846,1919-01,western,36,1,M8 D1,,wild-west-0846
k,a real magazine,EXCLUDE,book,5,,,jimjam,Jim Jam Jems,Jim Jam Jems,1921-04,humor,68,4,M3 A2,,jim-jam
,,LOOK,book,3,yes,42,argosy1897,Argosy,Argosy v25n01,1897-08,general,192,300,N1 A2,,argosy-1897
x,,LOOK,comic,3,,,fourcolor,Four Color,Four Color #20,1943,other,68,1,M7,,four-color
"""


class ApplyStep(unittest.TestCase):
    def test_decisions_are_read_with_and_without_accepting_the_proposal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.csv")
            open(p, "w", encoding="utf-8").write(CSV)
            exc, kept, und = sa.read_decisions(p)
            self.assertEqual([r["id"] for r in exc], ["plattner", "fourcolor"])
            self.assertEqual([r["id"] for r in kept], ["jimjam"])
            self.assertEqual([r["id"] for r in und], ["wildwest", "argosy1897"])
            exc, kept, und = sa.read_decisions(p, accept_proposed=True)
            self.assertEqual([r["id"] for r in exc], ["plattner", "wildwest", "fourcolor"])   # the proposed EXCLUDE unless k; LOOK only with x
            self.assertEqual([r["id"] for r in und], ["argosy1897"])

    def test_apply_writes_entries_once_and_refuses_unknown_ids(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "corpus_set_aside.json")
            json.dump({"_comment": "x", "issues": [{"id": "old", "ia_identifier": "old", "class": "download failed", "reason": "not downloadable"}]},
                      open(path, "w", encoding="utf-8"))
            rows = list(csv.DictReader(io.StringIO(CSV)))
            exc = [r for r in rows if r["decision"] == "x"]
            added, skipped = sa.apply(exc, "Sujin Kang", "2026-10-20", path=path, selection={"plattner", "fourcolor", "old"})
            self.assertEqual([e["id"] for e in added], ["plattner", "fourcolor"])
            self.assertEqual(skipped, [])
            d2 = json.load(open(path, encoding="utf-8"))
            self.assertEqual([e["id"] for e in d2["issues"]], ["old", "plattner", "fourcolor"])
            e = d2["issues"][1]
            self.assertEqual(e["class"], "not a magazine issue")
            self.assertTrue(e["reason"].startswith("not a magazine issue: a standalone book"))
            self.assertEqual(e["kind"], "book")
            self.assertEqual(e["note"], "a novel")
            self.assertEqual(e["decided_by"], "Sujin Kang")
            self.assertEqual(d2["decisions"][-1]["added"], 2)
            # again: nothing added, both skipped
            added, skipped = sa.apply(exc, "Sujin Kang", "2026-10-20", path=path, selection={"plattner", "fourcolor", "old"})
            self.assertEqual((added, skipped), ([], ["plattner", "fourcolor"]))
            # an id outside the selection stops everything
            with self.assertRaises(SystemExit):
                sa.apply(exc, "Sujin Kang", "2026-10-20", path=path, selection={"old"})
            # a dry run writes nothing
            json.dump({"issues": []}, open(path, "w", encoding="utf-8"))
            added, skipped = sa.apply(exc, "S", "2026-10-20", path=path, selection=None, dry_run=True)
            self.assertEqual(len(added), 2)
            self.assertEqual(json.load(open(path, encoding="utf-8"))["issues"], [])


def issue(iid, year, genre, mag="M"):
    return {"id": iid, "ia_identifier": iid, "magazine": mag, "cover_date": str(year), "year": year, "genre": genre, "undated": False}


class SampleReplacement(unittest.TestCase):
    def setUp(self):
        self.sample = {"seed": 7, "n": 4, "population": 100, "cells": [], "issues": [
            {"order": 1, "id": "book1", "ia_identifier": "book1", "magazine": "A Novel", "cover_date": "1897", "decade": "1890s", "genre": "science fiction", "genre_as_filed": "science fiction", "second_reader": False},
            {"order": 2, "id": "wt1", "ia_identifier": "wt1", "magazine": "Weird Tales", "cover_date": "1937-12", "decade": "1930s", "genre": "weird", "genre_as_filed": "weird", "second_reader": False},
            {"order": 3, "id": "dime1", "ia_identifier": "dime1", "magazine": "Wild West Weekly", "cover_date": "1919-01", "decade": "1910s", "genre": "western", "genre_as_filed": "western", "second_reader": False},
            {"order": 5, "id": "lone", "ia_identifier": "lone", "magazine": "Lone", "cover_date": "1905", "decade": "1900s", "genre": "romance", "genre_as_filed": "romance", "second_reader": True},
        ]}
        self.population = [issue("book1", 1897, "science fiction"), issue("sf2", 1898, "science fiction"), issue("sf3", 1899, "science fiction"),
                           issue("wt1", 1937, "weird"), issue("dime1", 1919, "western"), issue("w2", 1915, "western"), issue("w3", 1912, "western"),
                           issue("lone", 1905, "romance")]
        self.aside = {"book1": {"id": "book1", "class": "not a magazine issue", "reason": "not a magazine issue: a standalone book (a novel or a collection)"},
                      "dime1": {"id": "dime1", "class": "not a magazine issue", "reason": "not a magazine issue: a dime-novel or nickel-weekly number"}}

    def test_replacement_within_the_cell_keeps_order_and_second_reader(self):
        new, changes = dv.replace_set_aside(self.sample, self.population, self.aside, seed=7, now="2026-10-20T10:00:00")
        self.assertEqual([c[0] for c in changes], ["book1", "dime1"])
        self.assertEqual([c[3] for c in changes], [None, None])
        by_order = {i["order"]: i for i in new["issues"]}
        self.assertEqual(set(by_order), {1, 2, 3, 5})
        self.assertIn(by_order[1]["id"], ("sf2", "sf3"))
        self.assertEqual((by_order[1]["decade"], by_order[1]["genre"]), ("1890s", "science fiction"))
        self.assertEqual(by_order[1]["replaces"], "book1")
        self.assertIn(by_order[3]["id"], ("w2", "w3"))
        self.assertEqual(by_order[3]["second_reader"], False)
        self.assertEqual(by_order[2]["id"], "wt1")                       # untouched
        self.assertEqual(by_order[5]["id"], "lone")
        self.assertEqual(new["n"], 4)
        self.assertEqual([r["id"] for r in new["replaced"]], ["book1", "dime1"])
        self.assertEqual(new["replaced"][0]["replaced_by"], by_order[1]["id"])
        self.assertTrue(new["replaced"][1]["replaced_reason"].startswith("not a magazine issue: a dime-novel"))
        self.assertEqual(new["replacements_on"], ["2026-10-20T10:00:00"])
        # deterministic: the same call chooses the same replacements
        again, _ = dv.replace_set_aside(self.sample, self.population, self.aside, seed=7, now="2026-10-20T10:00:00")
        self.assertEqual([i["id"] for i in again["issues"]], [i["id"] for i in new["issues"]])

    def test_an_emptied_cell_gives_its_slot_to_the_decade_then_the_genre(self):
        aside = {"lone": {"id": "lone", "reason": "not a magazine issue: a standalone book"}}
        # nothing else in 1900s / romance; the decade has another issue
        pop = self.population + [issue("w1900", 1904, "western")]
        new, changes = dv.replace_set_aside(self.sample, pop, aside, seed=7)
        self.assertEqual(changes, [("lone", "w1900", ("1900s", "romance"), "decade")])
        slot = next(i for i in new["issues"] if i["order"] == 5)
        self.assertEqual((slot["decade"], slot["genre"], slot["second_reader"]), ("1900s", "western", True))
        self.assertIn("drawn from the decade", slot["cell_fallback"])
        self.assertIn("cell_fallback", new["replaced"][0])
        # nothing in the decade either: the genre
        pop = self.population + [issue("r1930", 1933, "romance")]
        new, changes = dv.replace_set_aside(self.sample, pop, aside, seed=7)
        self.assertEqual(changes[0][1:], ("r1930", ("1900s", "romance"), "genre"))
        # nothing anywhere: the slot is dropped and the file says so
        new, changes = dv.replace_set_aside(self.sample, self.population, aside, seed=7)
        self.assertEqual(changes, [("lone", None, ("1900s", "romance"), None)])
        self.assertEqual(new["n"], 3)
        self.assertIsNone(new["replaced"][0]["replaced_by"])
        self.assertIn("slot is dropped", new["replaced"][0]["note"])

    def test_a_replacement_is_never_an_issue_already_in_the_sample(self):
        pop = self.population + [issue("wt1", 1898, "science fiction")]    # the same id in the cell would be a duplicate slot
        new, changes = dv.replace_set_aside(self.sample, pop, self.aside, seed=7)
        ids = [i["id"] for i in new["issues"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_nothing_set_aside_changes_nothing(self):
        new, changes = dv.replace_set_aside(self.sample, self.population, {}, seed=7)
        self.assertEqual(changes, [])
        self.assertEqual([i["id"] for i in new["issues"]], ["book1", "wt1", "dime1", "lone"])


if __name__ == "__main__":
    unittest.main()
