"""toc_folio: the contents page and the printed page numbers (2026-10-15)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
import toc_folio as T  # noqa: E402


def page(no, regions, W=1000, H=1400):
    return {"page": no, "width": W, "height": H,
            "regions": [{"label": lab, "bbox": list(b), "order": i, "text": t, "lines": []} for i, (lab, b, t) in enumerate(regions)]}


class Helpers(unittest.TestCase):
    def test_selftest(self):
        T.selftest()

    def test_tail_number(self):
        self.assertEqual(T._split_tail_number("WEREWOLVES OF WAR D. W. HALL 153"), ("WEREWOLVES OF WAR D. W. HALL", 153))
        self.assertIsNone(T._split_tail_number("The Portal to Power (Part 3)")[1])
        self.assertIsNone(T._split_tail_number("(Novel — 35,000 words)")[1])

    def test_two_entries_in_one_line(self):
        self.assertEqual(len(T._split_two("Interglacial Forest in Alaska . . . 65 Sun Dance on Fort Hall Reservation . 98")), 2)
        self.assertEqual(len(T._split_two("THE CHASE AFTER LITTLE ALEC Karl Detzer 46")), 1)

    def test_names(self):
        self.assertTrue(T._looks_like_name("A. Merritt"))
        self.assertTrue(T._looks_like_name("Katharine van der Veer"))
        self.assertTrue(T._looks_like_name("H. P. Lovecraft and E. H. Price"))
        self.assertFalse(T._looks_like_name("The Camp-Fire"))
        self.assertIsNone(T.STAFF_RE.match("EDITOR'S PAGE"))
        self.assertIsNotNone(T.STAFF_RE.match("Editor"))

    def test_type_labels_keep_titles(self):
        self.assertEqual(T.TYPE_LABEL.sub(" ", "THE STORY TELLERS' CIRCLE").strip(), "THE STORY TELLERS' CIRCLE")
        self.assertEqual(T.TYPE_LABEL.sub(" ", "KAFFIR ORANGE (A Novelette)").strip(), "KAFFIR ORANGE")

    def test_prose(self):
        self.assertFalse(T._prose("The Voice of the Pack By Edison Marshall"))
        self.assertFalse(T._prose("The Trail of the Cloven Hoof . . . . ."))
        self.assertTrue(T._prose("Chester Fay, super-cracksman, comes back and serves a pal in this keenly exciting story."))

    def test_by_split(self):
        sp = T._split_title_author("The Affair at Tala'at By George A. Seel")
        self.assertEqual(sp[0][:2], ("The Affair at Tala'at", "George A. Seel"))
        sp = T._split_title_author("THE CATSPAW, by George O. Smith .....")
        self.assertEqual(sp[0][:2], ("THE CATSPAW", "George O. Smith"))
        sp = T._split_title_author("Big Timber Offered by Government")   # one word after 'by' is not a by-line
        sure = [x for x in sp if x[2] not in ("name", "tail")]
        self.assertEqual(sure[0][:2], ("Big Timber Offered by Government", ""))


class Contents(unittest.TestCase):
    def test_block_with_number_after_blank_and_title_alone(self):
        p = page(4, [("TableOfContents", (100, 200, 900, 900),
                      "The Roundup . . . . .\nThe Editor . . . . .\n6\n\n\nMissing . . . . .\n\n116\n\n\n"
                      "The Camp-Fire 89\nAsk Adventure 92\nTrail Ahead 96")])
        got = {(e["title"], e["page"]) for e in T.parse_contents_page(p)}
        self.assertIn(("Missing", 116), got)
        self.assertIn(("The Camp-Fire", 89), got)
        self.assertIn(("Ask Adventure", 92), got)

    def test_blurb_with_number_takes_title_above(self):
        p = page(4, [("SectionHeader", (100, 200, 500, 230), "The Cat from Siam"),
                     ("Text", (100, 240, 500, 270), "by Fredric Brown"),
                     ("Text", (100, 280, 900, 340), "Both the cop and a cat knew that Brian Carter just could not be the murderer—and when the cat began talking, the cop listened carefully! 9")])
        es = T.parse_contents_page(p)
        self.assertEqual([(e["title"], e["author"], e["page"]) for e in es], [("The Cat from Siam", "Fredric Brown", 9)])

    def test_chapter_list_is_not_pieces(self):
        rows = [("Text", (100, 100, 900, 130), "CONTENTS")]
        for k, (r, t, n) in enumerate([("I", "The Airplane Chums", 1), ("II", "Looking Forward", 14), ("III", "Good-bye", 22),
                                       ("IV", "Starting for France", 28), ("V", "The Secret Sailing", 38)]):
            rows.append(("Text", (100, 200 + 40 * k, 900, 230 + 40 * k), f"{r}—{t}..... {n}"))
        P = {s: page(s, [("Text", (100, 600, 900, 700), f"text {s}"), ("PageFooter", (480, 1350, 520, 1380), str(s - 5))])
             for s in range(6, 50)}
        P[4] = page(4, rows)
        R = T.analyse_issue(P)
        self.assertEqual(R["toc_kind"], "chapters")
        self.assertEqual(R["entries"], [])

class Speed(unittest.TestCase):
    """16 October 2026: three corpus issues held the 10b report for hours (rows of thousands of characters; spaced
    leader dots that do not end in a number)."""

    def test_long_and_dotted_lines_are_fast(self):
        import time
        prose = ("He bent, plunged his straw into the brilliancy that filled the vessel, and straightened up. " * 200).strip()
        dots = "THE LAST ROW " + " .  " * 60 + " by Nobody"
        pages = {1: page(1, [("Text", (50, 100, 950, 1300), prose)]),
                 2: page(2, [("SectionHeader", (50, 60, 950, 100), "CONTENTS"),
                             ("TableOfContents", (50, 120, 950, 900), "THE MAD WORLD by A. L. Burkholder..... 35\n" + dots + "\n"
                              "Two Worlds . . . . 41 The Third Man . . . . . . 52\n" + prose[:290])])}
        t0 = time.time()
        R = T.analyse_issue(pages)
        self.assertLess(time.time() - t0, 5)
        self.assertIsNone(T.TWO_RE.match(dots))
        self.assertTrue(isinstance(R["entries"], list))


if __name__ == "__main__":
    unittest.main()
