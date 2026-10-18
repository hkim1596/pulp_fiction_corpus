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
        self.assertIsNone(T.int_token("③"))                # circled digits (all_american_fiction_1938_02, 10b, 17 October)
        self.assertEqual(T.int_token("l2"), 12)

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

    def test_block_title_line_then_author_and_number_after_blank(self):
        # Weird Tales, December 1937 (toc_folio 1.3, 18 October): the title ends in its leader, the author and
        # the number are the next printed line, a blank line between; 2.7 read 'B. Wallis' as the title.
        p = page(4, [("TableOfContents", (78, 466, 1472, 1767),
                      "CONTENTS FOR DECEMBER, 1937\n\n\nThe Sea-Witch . . . . .\n\nNictzin Dyalhis 643\n\n\n"
                      "A saga of Heldra the lovely, who came from the sea to compass a weird revenge\n\n\n\n"
                      "The Voyage of the Neutralia (part 2) . . . . .\n\nB. Wallis 726\n\n\n"
                      "An epic of weird adventures and a strange voyage to other planets\n\n\n\n"
                      "Fragment . . . . .\n\nRobert E. Howard 748\n\n\nPosthumous verse\n\n\nThe Eyrie . . . . .\n\n760")])
        es = {e["title"]: e for e in T.parse_contents_page(p)}
        self.assertEqual((es["The Sea-Witch"]["author"], es["The Sea-Witch"]["page"]), ("Nictzin Dyalhis", 643))
        self.assertEqual((es["The Voyage of the Neutralia"]["author"], es["The Voyage of the Neutralia"]["page"]), ("B. Wallis", 726))
        self.assertEqual(es["The Voyage of the Neutralia"].get("part"), "part 2")
        self.assertEqual((es["Fragment"]["author"], es["Fragment"]["page"]), ("Robert E. Howard", 748))
        self.assertEqual(es["The Eyrie"]["page"], 760)
        self.assertNotIn("B. Wallis", es)
        self.assertEqual(es["The Voyage of the Neutralia"]["blurb"][:16], "An epic of weird")

    def test_title_line_then_author_in_capitals_with_number(self):
        # Adventure, September 1947 (toc_folio 1.3): each entry is a two-line heading box, 'Title .....' over
        # 'AUTHOR 102'; 2.7 read the capitals as the title and the title line as a piece without a number.
        p = page(5, [("SectionHeader", (211, 227, 1409, 264), "The Man Who Didn't Like Texas .....\nCLIFTON ADAMS 102"),
                     ("Text", (308, 262, 1328, 427), "Nobody ever dared ask Big Homer to cut short his fun when that toughest roustabout working the field was around."),
                     ("SectionHeader", (211, 427, 1409, 466), "A Pig for Mauna Loa .....\nCARL J. KUNZ 110"),
                     ("SectionHeader", (211, 656, 1409, 693), "When Your Number's Up .....\nEARL SUTTERFIELD 120"),
                     ("SectionHeader", (646, 1085, 972, 1124), "THE SERIAL"),
                     ("SectionHeader", (211, 1144, 1412, 1181), "He Who Rides the Tiger (4th of 5 parts) .....\nJAMES NORMAN 80"),
                     ("TableOfContents", (222, 1778, 1417, 1978),
                      "The Camp-Fire .....\nWhere readers, writers and adventurers meet 6\n\n\nAsk Adventure .....\nInformation you can't get elsewhere 131\n\n\nLost Trails .....\nWhere old paths cross 142")], W=1600, H=2100)
        es = {e["title"]: e for e in T.parse_contents_page(p)}
        self.assertEqual((es["The Man Who Didn't Like Texas"]["author"], es["The Man Who Didn't Like Texas"]["page"]), ("CLIFTON ADAMS", 102))
        self.assertEqual((es["A Pig for Mauna Loa"]["author"], es["A Pig for Mauna Loa"]["page"]), ("CARL J. KUNZ", 110))
        self.assertEqual((es["When Your Number's Up"]["author"], es["When Your Number's Up"]["page"]), ("EARL SUTTERFIELD", 120))
        tiger = es["He Who Rides the Tiger (4th of 5 parts)"]          # the marker stays in the title, as in 2.6
        self.assertEqual((tiger["author"], tiger["page"]), ("JAMES NORMAN", 80))
        self.assertEqual((es["The Camp-Fire"]["page"], es["The Camp-Fire"]["blurb"]), (6, "Where readers, writers and adventurers meet"))
        self.assertEqual((es["Lost Trails"]["page"], es["Lost Trails"]["blurb"]), (142, "Where old paths cross"))
        for wrong in ("CLIFTON ADAMS", "EARL SUTTERFIELD", "JAMES NORMAN", "Where old paths cross"):
            self.assertNotIn(wrong, es)

    def test_capital_author_after_leader_is_one_entry(self):
        # Argosy, November 1909 (toc_folio 1.3): 'TITLE.....AUTHOR..... 689' in a cell list; deglue broke the
        # line after the leader and the author's capitals became a title.
        p = page(5, [("ListGroup", (177, 1217, 1310, 1630),
                      "MR. BUTTERWORTH'S BABY.....C. LANGTON CLARKE..... 600\n\nHIS AFTER-DINNER SPEECH.....LEE BERTRAND..... 689\n\n"
                      "HUNTING DOWN ROXBURY STATION.....ROBERT KEENE..... 747\n\nWIRES AWRY.....FRITZ KROG..... 758")])
        got = {(e["title"], e["author"], e["page"]) for e in T.parse_contents_page(p)}
        self.assertEqual(got, {("MR. BUTTERWORTH'S BABY", "C. LANGTON CLARKE", 600), ("HIS AFTER-DINNER SPEECH", "LEE BERTRAND", 689),
                               ("HUNTING DOWN ROXBURY STATION", "ROBERT KEENE", 747), ("WIRES AWRY", "FRITZ KROG", 758)})
        self.assertEqual(T.deglue('He waited.THE NEXT TITLE..... 12'), 'He waited.\nTHE NEXT TITLE..... 12')   # a blurb run into a title still breaks

    def test_name_with_its_own_leader_under_the_title_and_a_year_in_a_title(self):
        # Western Story, July 1935 and Adventure, June 1928 (toc_folio 1.3): 'Title . . . . .' / 'Author . . .' / '8',
        # and 'In The Year 2000 . . . . .' / 'ARTHUR O. FRIEL' / '60' (2000 is not a page number).
        p = page(4, [("Text", (172, 667, 1383, 752), "Brick-top In Blue Rock . . . . .\nJohnston McCulley . . .\n8\n\n\nBrick-top Burke buys an interest in the Heart-break Ranch, sight unseen."),
                     ("Text", (172, 770, 1383, 865), "The Three Mesquiteers . . . . .\nWilliam Colt MacDonald . . .\n100\n\n\nPart Three\nThey walk into a cleverly arranged trap.")])
        got = {(e["title"], e["author"], e["page"]) for e in T.parse_contents_page(p)}
        self.assertEqual(got, {("Brick-top In Blue Rock", "Johnston McCulley", 8), ("The Three Mesquiteers", "William Colt MacDonald", 100)})
        p = page(3, [("TableOfContents", (126, 607, 1367, 1837),
                      "The Canal Diggers' Dinner . . .\nEDGAR YOUNG\n59\n\n\nIn The Year 2000 . . . . .\nARTHUR O. FRIEL\n60\n\n\nPart Two of a Novel of the Amazon Jungle\n\n\nKiwi . . . . .\nRAOUL F. WHITFIELD\n99")])
        got = {(e["title"], e["author"], e["page"]) for e in T.parse_contents_page(p)}
        self.assertEqual(got, {("The Canal Diggers' Dinner", "EDGAR YOUNG", 59), ("In The Year 2000", "ARTHUR O. FRIEL", 60), ("Kiwi", "RAOUL F. WHITFIELD", 99)})
        self.assertEqual(T._split_tail_number("In The Year 2000 . . . . ."), ("In The Year 2000 . . . . .", None))
        self.assertEqual(T._split_tail_number("The Camp-Fire ..... 13."), ("The Camp-Fire", 13))

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
