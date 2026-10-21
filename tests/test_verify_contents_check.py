"""The contents check (level 1 of the verification sample; Sujin Kang's decision 2 of 19 October 2026): the matching of
contents entries to the machine's records, the saved check and its figure, and the two pages (webapp/verify_pages.py).
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import verify_pages as VP


def rec(aid, type_, title, pages, author=None, toc=None, status="auto"):
    return {"article_id": aid, "type": type_, "title": title, "author": author, "pages": pages, "toc": toc, "status": status,
            "fragments": [{"page": p, "region_ids": [1]} for p in pages]}


TOC = [{"title": "The Voyage of the Neutralia", "author": "B. Wallis", "page": 726, "scan": 10, "type": "story", "section": "NOVELETTE"},
       {"title": "The Last Pharaoh", "author": "Thomas P. Kelley", "page": 740, "scan": 24, "type": "story"},
       {"title": "The Eyrie", "author": None, "page": 760, "scan": 44, "type": "letters"},
       {"title": "Ghost Lights", "author": "A. Poet", "page": 759, "scan": 43, "type": "poem"}]

ARTICLES = [rec("i_a001", "toc", "Contents", [3]),
            rec("i_a002", "story", "THE VOYAGE OF THE NEUTRALIA", [10, 11, 12], "B. Wallis", toc={"title": "The Voyage of the Neutralia"}),
            rec("i_a003", "ad", "Buy Bonds", [23]),
            rec("i_a004", "story", "The Last Pharaoh", [25, 26], "Thomas P. Kelley"),
            rec("i_a005", "feature", "The Eyrie: letters", [44, 45])]


class Matching(unittest.TestCase):
    def test_entries_find_their_records_three_ways(self):
        m = VP.match_entries(TOC, ARTICLES)
        self.assertEqual([(r["article_id"] if r else None) for _, r, _ in m], ["i_a002", "i_a004", "i_a005", None])
        hows = [h for _, _, h in m]
        self.assertEqual(hows[0], "the record's own contents entry")
        self.assertEqual(hows[1], "starts one page off")
        self.assertEqual(hows[2], "starts on the entry's page")
        self.assertIsNone(hows[3])

    def test_a_record_is_used_once_and_ads_never(self):
        toc = [{"title": "Buy Bonds", "page": 1, "scan": 23, "type": "feature"}, {"title": "The Last Pharaoh", "page": 740, "scan": 25, "type": "story"},
               {"title": "The Last Pharaoh (conclusion)", "page": 741, "scan": 25, "type": "story"}]
        m = VP.match_entries(toc, ARTICLES)
        self.assertIsNone(m[0][1])                                  # the advertisement is not a reading record
        self.assertEqual(m[1][1]["article_id"], "i_a004")
        self.assertIsNone(m[2][1])                                  # taken already

    def test_title_similarity_fallback(self):
        toc = [{"title": "Eyrie, The", "page": None, "scan": None, "type": "letters"}]
        m = VP.match_entries(toc, ARTICLES)
        self.assertEqual(m[0][1]["article_id"], "i_a005")
        self.assertTrue(m[0][2].startswith("a record with a title like it"))

    def test_summarize(self):
        s = VP.summarize(["right", "right", "fixed", "na", None, "missing"], missed=2, missed_right=1)
        self.assertEqual((s["n_entries"], s["n_listed"], s["n_right"], s["n_fixed"], s["n_missing"], s["n_na"]), (6, 4, 2, 1, 1, 1))
        self.assertEqual((s["pieces"], s["pieces_right"]), (6, 3))


class FakeEX:
    def __init__(self, con):
        self._con = con

    def db(self):
        return self._con

    @staticmethod
    def N(v):
        return f"\x01{v}"

    @staticmethod
    def _table(headers, rows, cls=""):
        return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


class NoClose(sqlite3.Connection):
    def close(self):        # the page closes its connection; the test keeps one in memory
        pass


class PagesAndLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        data = os.path.join(root, "data")
        os.makedirs(os.path.join(root, "config"))
        os.makedirs(os.path.join(data, "assembly_v2", "rules", "wt_1937_12"))
        json.dump({"toc": TOC, "toc_page": 3, "toc_pages": 1, "toc_kind": "pieces", "offset": 716, "pages": {}},
                  open(os.path.join(data, "assembly_v2", "rules", "wt_1937_12", "analysis.json"), "w"))
        json.dump({"seed": 1, "drawn": "2026-10-15T09:52:36", "population": 7306, "second_reader_every": 5, "issues": [
            {"order": 1, "id": "wt_1937_12", "ia_identifier": "x", "magazine": "Weird Tales", "cover_date": "1937-12", "decade": "1930s", "genre": "weird", "genre_as_filed": "weird", "second_reader": False},
            {"order": 2, "id": "other", "ia_identifier": "y", "magazine": "Argosy", "cover_date": "1919-09", "decade": "1910s", "genre": "general", "genre_as_filed": "general", "second_reader": True}]},
                  open(os.path.join(root, "config", "verification_sample.json"), "w"))
        con = sqlite3.connect(":memory:", factory=NoClose)
        con.row_factory = sqlite3.Row
        con.executescript("CREATE TABLE issues(id, records, stories, verified, modified, pages); CREATE TABLE records(issue, status, verified_by);")
        con.execute("INSERT INTO issues VALUES ('wt_1937_12', 40, 12, 40, '', 132)")
        con.execute("INSERT INTO issues VALUES ('other', 30, 10, 3, '', 196)")
        con.execute("INSERT INTO records VALUES ('other', 'verified', 'sujin')")
        self.docs = {"wt_1937_12": {"articles": ARTICLES}}
        VP._G.clear()
        VP._CACHE.update({"sample": (None, None), "checks": (None, None)})
        VP.bind({"ROOT": root, "DATA": data, "EX": FakeEX(con), "esc": lambda s: str(s) if s is not None else "", "howto": lambda t: f"<details>{t}</details>",
                 "display_name": lambda u: u.title(), "issue_by_id": lambda iid: {"id": iid, "magazine": "Weird Tales", "cover_date": "1937-12"},
                 "effective_doc": lambda iid: self.docs.get(iid)})

    def tearDown(self):
        self.tmp.cleanup()

    def test_contents_page_shows_entries_scan_and_records(self):
        # toc_pages as the list of scans (what the assembler writes for a contents spread) and as a count
        p = os.path.join(VP._G["DATA"], "assembly_v2", "rules", "wt_1937_12", "analysis.json")
        a = json.load(open(p)); a["toc_pages"] = [3, 4]; json.dump(a, open(p, "w"))
        self.assertEqual(VP.contents_reading("wt_1937_12")["toc_scans"], [3, 4])
        a["toc_pages"] = 2; json.dump(a, open(p, "w"))
        self.assertEqual(VP.contents_reading("wt_1937_12")["toc_scans"], [3, 4])
        a["toc_pages"] = 1; json.dump(a, open(p, "w"))
        page = VP.contents_page("wt_1937_12", {}, "sujin")
        self.assertIn("/img/wt_1937_12/page_0003.png", page)
        self.assertIn("The Voyage of the Neutralia", page)
        self.assertIn("/article/i_a002", page)
        self.assertIn("no record found for it", page)              # Ghost Lights
        self.assertIn("name='v_0'", page)
        self.assertIn("slot #1", page)
        self.assertIn("Save the check", page)
        guest = VP.contents_page("wt_1937_12", {}, "guest")
        self.assertIn("disabled", guest)
        self.assertNotIn("Save the check", guest)
        self.assertIn("No assembly on disk", VP.contents_page("nothing", {}, "sujin"))

    def test_a_check_is_appended_and_counted(self):
        form = {"issue": "wt_1937_12", "v_0": "right", "v_1": "fixed", "v_2": "right", "v_3": "missing", "n_3": "no record; made one",
                "missed": "1", "missed_right": "1", "unlisted": "2", "note": "fine issue"}
        rec = VP.append_check("sujin", lambda k, d="": form.get(k, d))
        self.assertEqual(rec["issue"], "wt_1937_12")
        self.assertEqual((rec["n_right"], rec["n_fixed"], rec["n_missing"], rec["n_na"]), (2, 1, 1, 0))
        self.assertEqual((rec["pieces"], rec["pieces_right"]), (5, 3))
        self.assertEqual(rec["entries"][0]["record"], "i_a002")
        self.assertEqual(rec["entries"][3]["note"], "no record; made one")
        self.assertEqual((rec["order"], rec["decade"], rec["genre"]), (1, "1930s", "weird"))
        self.assertTrue(rec["complete"])
        lines = open(VP.checks_path(), encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 1)
        lc = VP.latest_checks()
        self.assertEqual(list(lc), ["wt_1937_12"])
        fig = VP.figure(list(VP.one_per_issue(lc).values()))
        self.assertEqual((fig["issues"], fig["pieces"], fig["right"]), (1, 5, 3))
        # a second reader: counted for agreement, not for the figure; the first reader's later check replaces hers
        VP.append_check("heejin", lambda k, d="": {"issue": "wt_1937_12", "v_0": "right", "v_1": "right", "v_2": "right", "v_3": "missing"}.get(k, d))
        VP.append_check("sujin", lambda k, d="": dict(form, v_1="right").get(k, d))
        lc = VP.latest_checks()
        counted = VP.one_per_issue(lc)
        self.assertEqual(counted["wt_1937_12"]["user"], "sujin")
        self.assertEqual(counted["wt_1937_12"]["n_right"], 3)
        ag = VP.reader_agreement(lc)
        self.assertEqual((ag["issues"], ag["entries"], ag["same"]), (1, 4, 4))
        # the page shows the saved check and prefills the reader's verdicts
        page = VP.contents_page("wt_1937_12", {}, "sujin")
        self.assertIn("Your last check", page)
        self.assertIn("Checked by Heejin", page)
        self.assertIn("value='right' checked", page)
        # a form without an issue writes nothing
        self.assertIsNone(VP.append_check("sujin", lambda k, d="": ""))

    def test_sample_page_has_the_contents_column_and_figure(self):
        page = VP.sample_page({}, "sujin")
        self.assertIn("/verify/contents/wt_1937_12", page)
        self.assertIn("<b>check</b>", page)
        self.assertIn("No issue checked yet", page)
        self.assertIn("contents check to do", page)
        VP.append_check("sujin", lambda k, d="": {"issue": "wt_1937_12", "v_0": "right", "v_1": "right", "v_2": "fixed", "v_3": "na"}.get(k, d))
        page = VP.sample_page({"checked": ["wt_1937_12"]}, "sujin")
        self.assertIn("checked</span>", page)
        self.assertIn("2/3 right", page)
        self.assertIn("1 of 2 issues checked", page)
        self.assertIn("66.7%", page)
        self.assertIn("Saved: the contents check of wt_1937_12", page)
        todo = VP.sample_page({"show": ["contents"]}, "sujin")
        self.assertNotIn("/verify/contents/wt_1937_12'><b>check</b>", todo)
        self.assertIn("/verify/contents/other", todo)
        self.assertIn("contents check so far: 1 issues, 2 of 3", VP.run_lines())


if __name__ == "__main__":
    unittest.main()
