"""The 2.8 upgrade's guard, cases 'retired_contents_misreading' and 'transferred_same_title_{trimmed,grown}' (p52, 19
October 2026). Shapes from the refused issues of 10f: Weird Tales 1949-11, whose 2.6 contents reading was a book club's
numbered list ('*41. Minions of the Moon—Beyer'), the fake pieces cutting the real stories (The Underbody, pp. 8–18);
Amazing Stories, whose 2.6 titles were by-lines ('By David M. Speaker'); Wonder Stories, whose titles were whole contents
lines ('THE LUNAR CONSUL by Sidney Patszer'); Argosy 1919-09, whose 2.6 serial record swallowed the three stories after
it; the Popular Magazine's two-box contents stubs grown into the whole piece.
"""
import copy
import sys
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import upgrade_continuity as u

FIELDS = ("work_id", "work_title", "serial", "excerpt_of", "contains_excerpt")


def rec(aid, type_, title, boxes, author=None, **extra):
    r = {"article_id": aid, "type": type_, "title": title, "author": author, "flags": [], "roles": {},
         "fragments": [{"page": p, "region_ids": sorted(i for q, i in boxes if q == p)} for p in sorted({q for q, _ in boxes})]}
    r.update(extra)
    return r


def boxes(page, n, start=0):
    return [(page, i) for i in range(start, start + n)]


def contents_serial(title, part=None, work=None):
    return {"serial": {"part_label": part, "part_n": None, "part_total": None, "source": "contents"},
            "work_id": work or ("mag:" + u.normalized(title).replace(" ", "_")), "toc": {"title": title, "page": 3}}


def run(old, cand):
    audit = []
    n = u.preserve_links({"articles": old}, {"articles": cand, "furniture": [], "unsorted": []}, audit=audit)
    return n, audit


class JunkTitles(unittest.TestCase):
    def test_what_counts_as_junk(self):
        self.assertEqual(u.junk_contents_title("*41. Minions of the Moon—Beyer"), "a numbered list item")
        self.assertEqual(u.junk_contents_title("*62. Incredible Planet", "Hubbard"), "a numbered list item")   # the surname does not save it
        self.assertEqual(u.junk_contents_title("64."), "no letters")
        self.assertEqual(u.junk_contents_title("Xxi."), "a chapter number")
        self.assertEqual(u.junk_contents_title("SERIALS"), "a section word")
        self.assertIsNone(u.junk_contents_title("SERIALS", "R. G. Emery"))        # with an author the story is identifiable
        self.assertIsNone(u.junk_contents_title("The Underbody"))
        self.assertIsNone(u.junk_contents_title("64 Hours to Midnight"))


class ContentsMisreading(unittest.TestCase):
    def test_a_book_clubs_list_read_as_the_contents_is_retired(self):
        # 2.6: list items as stories on pp. 5–8; "32. Works of M. P. Shiel" took pp. 8–18 of The Underbody
        old = [rec("i_a008", "story", "*41. Minions of the Moon—Beyer", boxes(5, 2), **contents_serial("*41. Minions of the Moon—Beyer")),
               rec("i_a015", "story", "32. Works of M. P. Shiel—(non-fiction)", boxes(8, 6, 2) + boxes(9, 10), **contents_serial("32. Works of M. P. Shiel")),
               rec("i_a018", "story", "*62. Incredible Planet", boxes(22, 4), "Hubbard", **contents_serial("*62. Incredible Planet")),
               rec("i_a019", "story", "64.", boxes(23, 5), **contents_serial("64.", part="Final"))]
        cand = [rec("i_b001", "ad", "The Fantasy Fiction Field Book Club", boxes(5, 4)),
                rec("i_b011", "story", "The Underbody", boxes(8, 8) + boxes(9, 10), "Allison V. Harding"),
                rec("i_b013", "story", "Conscience Maketh Cowards", boxes(21, 3) + boxes(22, 4) + boxes(23, 5), "Seabury Quinn")]
        n, audit = run(old, cand)
        self.assertEqual(n, 0)
        self.assertEqual([a["kind"] for a in audit], ["retired_contents_misreading"] * 4)
        self.assertEqual(audit[0]["proof"]["how"], "the old title is a numbered list item")
        self.assertEqual(audit[1]["proof"]["holder"]["title"], "The Underbody")
        self.assertEqual(audit[3]["proof"]["how"], "the old title is no letters")
        self.assertEqual(audit[3]["original_metadata"]["serial"]["part_label"], "Final")
        self.assertIsNone(cand[1].get("serial"))                      # nothing is attached to the real story

    def test_a_byline_or_a_whole_contents_line_for_a_title_is_retired_when_the_candidate_reads_the_piece(self):
        old = [rec("i_a010", "story", "By David M. Speaker", boxes(43, 5) + boxes(44, 6), **contents_serial("By David M. Speaker", part="Part III")),
               rec("i_a020", "story", "THE LUNAR CONSUL by Sidney Patszer", boxes(9, 8) + boxes(10, 8), **contents_serial("THE LUNAR CONSUL by Sidney Patszer", part="Part One")),
               rec("i_a030", "story", "Nathan Clover", boxes(99, 6), **contents_serial("Nathan Clover", part="Conclusion"))]
        cand = [rec("i_b010", "story", "The Message from Space", boxes(43, 5) + boxes(44, 6) + boxes(45, 3), "David M. Speaker"),
                rec("i_b020", "story", "The Lunar Consul", boxes(9, 8) + boxes(10, 8) + boxes(11, 8), "Sidney Patszer",
                    serial={"part_label": "Part One", "part_n": 1, "part_total": None, "source": "contents"}),
                rec("i_b030", "story", "The Road That Never Ends", boxes(99, 6) + boxes(100, 4), "Nathan Clover")]
        n, audit = run(old, cand)
        self.assertEqual(n, 0)
        self.assertEqual([a["proof"]["how"] for a in audit],
                         ["the old title is the holder's by-line", "the old title is a contents line containing the holder's title", "the old title is the holder's author"])
        self.assertEqual(cand[1]["serial"]["part_label"], "Part One")   # the candidate's own reading stays

    def test_the_reverse_fault_stays_refused(self):
        # 2.8 titled the story with the author's name (a section head above the by-line): the old record is the better one
        # (with identical boxes the general identity path would carry the metadata over; here the candidate's cut differs)
        old = [rec("i_a013", "story", "SERIALS", boxes(10, 20), "R. G. EMERY", **contents_serial("SERIALS"))]
        cand = [rec("i_b013", "story", "R. G. Emery", boxes(10, 18)), rec("i_b014", "ad", "Buy Bonds", boxes(10, 2, 18))]
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run(old, cand)
        # and a by-line title whose author is not the holder's: the candidate merged the piece into another
        old = [rec("i_a010", "story", "By A. Hyatt Verill", boxes(6, 5) + boxes(7, 6), **contents_serial("By A. Hyatt Verill", part="Conclusion"))]
        cand = [rec("i_b005", "feature", "Imagination and Reality", boxes(5, 4) + boxes(6, 5) + boxes(7, 6), "Hugo Gernsback")]
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run(old, cand)

    def test_links_and_provenance_are_never_retired_here(self):
        base = rec("i_a010", "story", "By David M. Speaker", boxes(43, 5), **contents_serial("By David M. Speaker", part="Part III"))
        cand = [rec("i_b010", "story", "The Message from Space", boxes(43, 5) + boxes(44, 6), "David M. Speaker")]
        for change in ({"prev": {"issue": "older"}}, {"next": {"issue": "newer"}}, {"part_n_source": {"issue": "trusted"}}):
            old = copy.deepcopy(base)
            old["serial"].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "cannot safely retain"):
                run([old], copy.deepcopy(cand))
        old = copy.deepcopy(base)
        old["excerpt_of"] = {"issue": "other", "title": "Quoted"}
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run([old], copy.deepcopy(cand))

    def test_a_lost_box_or_a_small_holder_refuses(self):
        old = [rec("i_a010", "story", "By David M. Speaker", boxes(43, 10), **contents_serial("By David M. Speaker"))]
        cand = [rec("i_b010", "story", "The Message from Space", boxes(43, 5), "David M. Speaker"),
                rec("i_b011", "story", "Another Tale", boxes(43, 5, 5), "Someone Else")]
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):          # the holder has half, not 60 percent
            run(old, cand)
        cand = [rec("i_b010", "story", "The Message from Space", boxes(43, 9), "David M. Speaker")]
        audit = []
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):          # one box unsorted
            u.preserve_links({"articles": old}, {"articles": cand, "furniture": [], "unsorted": [{"key": "43:9"}]}, audit=audit)


class SameTitleResized(unittest.TestCase):
    def test_a_record_that_swallowed_the_following_stories_is_trimmed(self):
        # Argosy 1919-09: 'CHAPTERS IX-XIII LUCK. In Six Parts. Part VI' held pp. 134–183; 2.8 cut the three stories after it
        old = [rec("i_a041", "story", "CHAPTERS IX-XIIILUCK. In Six Parts. Part VI", boxes(134, 10) + boxes(135, 10) + boxes(136, 10) + boxes(137, 10),
                   "JOHN FREDERICK", **contents_serial("LUCK. In Six Parts", part="Part VI", work="argosy:luck"))]
        cand = [rec("i_b041", "story", "CHAPTERS IX-XIIILUCK. In Six Parts", boxes(134, 10) + boxes(135, 4), "JOHN FREDERICK"),
                rec("i_b042", "story", "The Race of the Giants", boxes(135, 6, 4) + boxes(136, 10), "GIBBS HUNTLY"),
                rec("i_b043", "story", "Hands", boxes(137, 10), "GILBERT RIDDELL")]
        n, audit = run(old, cand)
        self.assertEqual(n, 1)
        self.assertEqual(audit[0]["kind"], "transferred_same_title_trimmed")
        self.assertEqual(cand[0]["work_id"], "argosy:luck")
        self.assertEqual(cand[0]["serial"]["part_label"], "Part VI")
        self.assertNotIn("work_id", cand[1])

    def test_a_trim_into_an_advertisement_or_an_untitled_record_refuses(self):
        old = [rec("i_a041", "story", "Handicaps", boxes(154, 10) + boxes(155, 10), "THOMSON BURTIS", **contents_serial("Handicaps", part="Part II"))]
        cand = [rec("i_b041", "story", "Handicaps", boxes(154, 10) + boxes(155, 2), "THOMSON BURTIS"),
                rec("i_b042", "ad", "Buy Bonds", boxes(155, 8, 2))]
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run(old, copy.deepcopy(cand))
        cand[1] = rec("i_b042", "story", "Chapter II", boxes(155, 8, 2))          # a start with no author read
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run(old, copy.deepcopy(cand))
        cand[1] = rec("i_b042", "story", "The Next Story", boxes(155, 8, 2), "Jane Doe")
        n, audit = run(old, cand)
        self.assertEqual((n, audit[0]["kind"]), (1, "transferred_same_title_trimmed"))

    def test_a_contents_stub_grown_into_the_whole_piece(self):
        # The Popular Magazine: 'Lost Wagons.' was a two-box stub placed from the contents; 2.8 found the whole piece
        old = [rec("i_a050", "story", "Lost Wagons.", boxes(155, 2), **contents_serial("Lost Wagons", part="Part IV"))]
        cand = [rec("i_b050", "feature", "Lost Wagons", boxes(155, 12) + boxes(156, 10), "Dane Coolidge")]
        n, audit = run(old, cand)
        self.assertEqual(n, 1)
        self.assertEqual(audit[0]["kind"], "transferred_same_title_grown")
        self.assertEqual(cand[0]["serial"]["part_label"], "Part IV")
        # a different author under the same title is another piece
        cand = [rec("i_b050", "story", "Lost Wagons", boxes(155, 12), "Someone Else")]
        old[0]["author"] = "Dane Coolidge"
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run(old, cand)

    def test_the_part_marker_difference_needs_both_authors(self):
        old = [rec("i_a041", "story", "Luck. Part VI", boxes(134, 10) + boxes(135, 10), None, **contents_serial("Luck", part="Part VI"))]
        cand = [rec("i_b041", "story", "Luck", boxes(134, 10), "JOHN FREDERICK"),
                rec("i_b042", "story", "The Race of the Giants", boxes(135, 10), "GIBBS HUNTLY")]
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            run(old, copy.deepcopy(cand))
        old[0]["author"] = "John Frederick"
        n, audit = run(old, cand)
        self.assertEqual((n, audit[0]["kind"]), (1, "transferred_same_title_trimmed"))


if __name__ == "__main__":
    unittest.main()
