"""Regression checks for lossless story assembly around layout interruptions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules
from layout_order import order_region_keys

PROSE = ("He watched the road while she stood beside him. They had travelled far "
         "and he was certain that his friends were waiting beyond the forest. ") * 3


def region(text, bbox, label="Text"):
    return {"text": text, "bbox": bbox, "label": label, "order": 0}


def page(n, regions):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": regions + [region(str(n), [450, 1320, 500, 1350], "PageFooter")]}


def start(n, title="THE LONG ROAD"):
    return page(n, [region(title, [80, 120, 920, 210], "SectionHeader"),
                    region("By Jane Smith", [100, 220, 450, 255]),
                    region(PROSE, [80, 300, 920, 1200])])


def ad(n):
    return page(n, [region("Learn at home", [80, 100, 900, 180], "SectionHeader"),
                    region("FREE coupon catalog. Send for FREE catalog. Write for FREE coupon. "
                           "Address Dept. 7. Money back guarantee. Mail $1 postpaid.", [80, 230, 900, 1150])])


class ContinuityTest(unittest.TestCase):
    def test_wrapped_and_abbreviated_continuation_notices(self):
        for text in ("Continued\nfrom page\n72", "Cont'd from p. 72", "Cont. from 72"):
            self.assertEqual(rules.CONT_FROM_RE.search(text).group(1), "72")
        for text in ("Continued\non page\n80", "Cont’d on p. 80", "Cont. on 80"):
            self.assertEqual(rules.CONT_TO_PAGE_RE.search(text).group(1), "80")
            self.assertIsNotNone(rules.CONT_ON_RE.search(text))

    def assemble(self, pages):
        out = rules.assemble("fixture", pages, rules.analyse(pages))
        self.assertEqual(out["checks"]["unassigned_regions"], 0)
        self.assertEqual(out["checks"]["double_owned_regions"], 0)
        return out["articles"]

    def test_single_box_after_multiple_ad_pages(self):
        pages = {1: start(1), 2: ad(2), 3: ad(3), 4: page(4, [region(PROSE, [80, 200, 920, 1200])])}
        recs = self.assemble(pages)
        stories = [r for r in recs if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["pages"], [1, 4])
        self.assertEqual(stories[0]["text"].count("He watched"), 6)
        self.assertNotIn("coupon", stories[0]["text"])
        self.assertNotIn("\n4", stories[0]["text"])

    def test_new_story_after_ad_is_not_resumed(self):
        recs = self.assemble({1: start(1), 2: ad(2), 3: start(3, "THE DARK SEA")})
        stories = [r for r in recs if r["type"] == "story"]
        self.assertEqual([r["pages"] for r in stories], [[1], [3]])

    def test_service_opening_before_narrative_does_not_resume_old_story(self):
        heading = ("A Free Question and Answer Service Bureau of Information on Outdoor Life and "
                   "Activities Everywhere and Upon the Various Commodities Required Therein. "
                   "Conducted for Adventure Magazine by Our Staff of Experts.")
        service = page(3, [region(heading, [80, 100, 920, 180], "PageHeader"),
                           region("QUESTIONS concerning local conditions and equipment should include all relevant "
                                  "details and sufficiently accurate information for useful responses.", [80, 200, 920, 400]),
                           region(PROSE, [80, 450, 920, 1200])])
        recs = self.assemble({1: start(1), 2: ad(2), 3: service})
        old = next(r for r in recs if r["title"] == "The Long Road")
        self.assertEqual(old["pages"], [1])
        self.assertNotIn("QUESTIONS", old["text"])

    def test_generic_resume_stops_at_explicit_questionnaire_instructions(self):
        p = page(3, [region("Instructions for accurate completion of this questionnaire include full details "
                            "about local conditions and equipment availability.", [80, 100, 920, 400]),
                     region(PROSE, [80, 450, 920, 1200])])
        info = rules.analyse({3: p})["pages"][3]
        self.assertIn("service opening", rules.generic_resume_blocker(p, info))

    def test_generic_resume_stops_at_known_department_or_announcement(self):
        for heading in ("Ask Adventure", "COMING NEXT MONTH"):
            with self.subTest(heading=heading):
                p = page(3, [region(heading, [80, 100, 920, 160], "SectionHeader"),
                             region(PROSE, [80, 250, 920, 1200])])
                info = {"starts": [], "furniture": [], "chapters": []}
                with patch.object(rules, "department_of", side_effect=lambda magazine, title:
                                  ("Ask Adventure", "feature", None) if title == "Ask Adventure" else None):
                    self.assertIsNotNone(rules.generic_resume_blocker(p, info, "Adventure"))

    def test_generic_resume_allows_caption_ornament_and_chapter_prefix(self):
        p = page(3, [region("The free road through the hills", [80, 100, 920, 180], "Caption"),
                     region("* * *", [80, 200, 920, 220]),
                     region("CHAPTER II", [80, 240, 920, 290], "SectionHeader"),
                     region(PROSE, [80, 340, 920, 1200])])
        recs = self.assemble({1: start(1), 2: ad(2), 3: p})
        old = next(r for r in recs if r["title"] == "The Long Road")
        self.assertEqual(old["pages"], [1, 3])

    def test_explicit_destination_can_start_with_descriptive_nonprose(self):
        first = start(1)
        first["regions"].insert(3, region("Continued on page 4", [100, 1220, 500, 1260]))
        opening = "Across the black valley stretched endless ridges under moonlight and mist, beyond any civilized frontier."
        target = page(4, [region(opening, [80, 100, 920, 400]),
                          region(PROSE, [80, 450, 920, 1200])])
        self.assertFalse(rules.prose_like(target["regions"][0]))
        recs = self.assemble({1: first, 2: ad(2), 3: ad(3), 4: target})
        old = next(r for r in recs if r["title"] == "The Long Road")
        self.assertEqual(old["pages"], [1, 4])
        self.assertIn(opening, old["text"])

    def test_forward_notice_resumes_across_another_story(self):
        first = start(1)
        first["regions"].insert(3, region("Continued on page 4", [100, 1220, 500, 1260]))
        recs = self.assemble({1: first, 2: start(2, "THE DARK SEA"), 3: ad(3),
                              4: page(4, [region(PROSE, [80, 200, 920, 1200])])})
        stories = {r["title"]: r for r in recs if r["type"] == "story"}
        self.assertEqual(stories["The Long Road"]["pages"], [1, 4])
        self.assertEqual(stories["The Dark Sea"]["pages"], [2])
        self.assertNotIn("Continued on", stories["The Long Road"]["text"])

    def test_paragraph_mislabelled_as_header_is_retained(self):
        p = start(1)
        p["regions"][2]["label"] = "PageHeader"
        story = next(r for r in self.assemble({1: p}) if r["type"] == "story")
        self.assertIn("He watched", story["text"])
        self.assertTrue(rules.is_furniture(p, 3, p["regions"][3]))

    def test_two_columns_with_spanning_title_preserve_ids(self):
        p = page(8, [region("THE LONG ROAD", [100, 40, 900, 100], "SectionHeader"),
                     region(PROSE, [80, 140, 460, 500]),
                     region(PROSE, [540, 140, 920, 500]),
                     region(PROSE, [80, 560, 460, 1000]),
                     region(PROSE, [540, 560, 920, 1000])])
        original = copy.deepcopy(p)
        self.assertEqual(order_region_keys({8: p}, [(8, i) for i in range(6)]),
                         [(8, i) for i in [0, 1, 3, 2, 4, 5]])
        self.assertEqual(p, original)

    def test_three_columns_and_corrected_page_sequence(self):
        regs = [region(PROSE, [x, y, x + 240, y + 250])
                for y in (100, 450) for x in (50, 380, 710)]
        pages = {9: page(9, regs), 7: page(7, regs)}
        result = order_region_keys(pages, [(pn, i) for pn in (9, 7) for i in range(6)])
        self.assertEqual(result, [(pn, i) for pn in (9, 7) for i in [0, 3, 1, 4, 2, 5]])

    def test_ambiguous_or_missing_geometry_retains_all_boxes(self):
        p = page(1, [region(PROSE, [80, 200, 800, 900]), region(PROSE, [100, 300, 900, 1000])])
        keys = [(1, 1), (1, 0)]
        self.assertEqual(order_region_keys({1: p}, keys), keys)
        del p["regions"][0]["bbox"]
        self.assertEqual(order_region_keys({1: p}, keys), keys)

    def test_small_contents_heading_preserves_previous_story_ending(self):
        """A readable small heading beats the fallback to the page's first box."""
        second = page(2, [region(PROSE, [80, 140, 920, 640]),
                          region("THE FLAG OF THE ALAMO", [270, 750, 730, 772], "SectionHeader"),
                          region(PROSE.replace("road", "fortress"), [80, 830, 920, 1230])])
        self.assertFalse(rules.is_display(second, second["regions"][1]))
        entry = {"title": "The Flag of the Alamo", "author": None, "page": 2,
                 "blurb": "", "section": "BRIEF FACT STORIES", "type": "story"}
        with patch.object(rules, "parse_toc", return_value=([entry], None)):
            recs = self.assemble({1: start(1), 2: second})
        stories = {r["title"]: r for r in recs if r["type"] == "story"}
        owned = lambda r: {(f["page"], i) for f in r["fragments"] for i in f["region_ids"]}
        self.assertIn((2, 0), owned(stories["The Long Road"]))
        self.assertNotIn((2, 0), owned(stories["The Flag of the Alamo"]))
        self.assertIn((2, 2), owned(stories["The Flag of the Alamo"]))

    def test_forward_destination_prevents_premature_suspended_resume(self):
        """A known later destination must outrank the generic after-ad heuristic."""
        first = start(1)
        first["regions"].insert(3, region("Continued on page 5", [100, 1220, 500, 1260]))
        unrelated = page(3, [region(PROSE.replace("road", "river"), [80, 180, 920, 1200])])
        final = page(5, [region(PROSE, [80, 180, 920, 1200])])
        recs = self.assemble({1: first, 2: ad(2), 3: unrelated, 4: ad(4), 5: final})
        old = next(r for r in recs if r["title"] == "The Long Road")
        self.assertEqual(old["pages"], [1, 5])
        self.assertNotIn("river", old["text"])

    def test_forward_destination_preserves_short_ending_before_next_title(self):
        first = start(1)
        first["regions"].insert(3, region("Continued on page 4", [100, 1220, 500, 1260]))
        ending = "He had finally come home."
        final = page(4, [region(ending, [80, 140, 920, 190]),
                         region("THE DARK SEA", [80, 250, 920, 340], "SectionHeader"),
                         region("By Jane Smith", [100, 350, 450, 385]),
                         region(PROSE.replace("road", "sea"), [80, 430, 920, 1200])])
        recs = self.assemble({1: first, 2: ad(2), 3: ad(3), 4: final})
        stories = {r["title"]: r for r in recs if r["type"] == "story"}
        old, new = stories["The Long Road"], stories["The Dark Sea"]
        self.assertEqual(old["pages"], [1, 4])
        self.assertIn(ending, old["text"])
        self.assertNotIn(0, next(f["region_ids"] for f in new["fragments"] if f["page"] == 4))

    def test_narrow_gutter_preserves_complete_columns_and_identity(self):
        regs = [region("Left upper. " + PROSE, [40, 100, 495, 400]),
                region("Right upper. " + PROSE, [500, 100, 950, 400]),
                region("Left lower. " + PROSE, [40, 450, 495, 1000]),
                region("Right lower. " + PROSE, [500, 450, 950, 1000])]
        p = page(1, regs)
        before = copy.deepcopy(p)
        result = order_region_keys({1: p}, [(1, i) for i in range(4)])
        self.assertEqual(result, [(1, i) for i in [0, 2, 1, 3]])
        self.assertEqual(p, before)
        self.assertEqual([p["regions"][i]["text"].split(".")[0] for _, i in result],
                         ["Left upper", "Left lower", "Right upper", "Right lower"])

    def test_numbered_panels_keep_explicit_row_sequence(self):
        regs = [region(f"{i + 1}. " + PROSE, [x, y, x + 400, y + 350])
                for i, (x, y) in enumerate([(50, 100), (550, 100), (50, 650), (550, 650)])]
        p = page(1, regs)
        sequence = [(1, i) for i in range(4)]
        self.assertEqual(order_region_keys({1: p}, sequence), sequence)

    def test_ownership_statement_is_a_separate_lower_band(self):
        p = page(1, [region("Left story. " + PROSE, [50, 100, 450, 900]),
                     region("STATEMENT OF OWNERSHIP", [50, 1000, 450, 1035], "SectionHeader"),
                     region("Left legal statement. " + PROSE, [50, 1060, 450, 1250]),
                     region("Right story. " + PROSE, [550, 100, 950, 900]),
                     region("Right legal statement. " + PROSE, [550, 1060, 950, 1250])])
        result = order_region_keys({1: p}, [(1, i) for i in range(5)])
        self.assertEqual(result, [(1, i) for i in [0, 3, 1, 2, 4]])

    def test_slightly_overlapping_columns_do_not_interleave_good_ocr_order(self):
        """OCR boxes can overlap by a pixel even when the text has two columns."""
        p = page(1, [region(PROSE, [50, 100, 501, 400]),
                     region(PROSE, [50, 500, 501, 1000]),
                     region(PROSE, [500, 100, 950, 400]),
                     region(PROSE, [500, 500, 950, 1000])])
        good_ocr_order = [(1, i) for i in range(4)]
        self.assertEqual(order_region_keys({1: p}, good_ocr_order), good_ocr_order)

    def test_short_sidebar_does_not_split_intro_or_its_own_list(self):
        """Real layout of the p126 Baffler Contest in td_1932_02."""
        p = {"page": 126, "width": 1462, "height": 2200, "regions": [
            region("Can You Solve This Mystery?", [134.504, 134.2, 1350.888, 242.0], "SectionHeader"),
            region("Win A Cash Prize in this Thrilling Contest", [222.224, 255.2, 1255.858, 321.2], "SectionHeader"),
            region("THRILLING DETECTIVE here prints the fourth Baffler in its new contest feature.", [134.504, 393.8, 954.686, 470.8]),
            region("Enter this contest—and see whether you can win one of the prizes for the best answers.", [134.504, 484.0, 954.686, 563.2]),
            region("The rules, which are extremely simple, follow:", [162.282, 576.4, 824.568, 618.2]),
            region("THE PRIZES:", [1019.014, 400.4, 1309.952, 440.0], "SectionHeader"),
            region("First Prize.....$15", [981.002, 453.2, 1346.502, 497.2]),
            region("Second Prize ..... 10", [981.002, 510.4, 1346.502, 554.4]),
            region("Third Prize..... 5", [981.002, 572.0, 1346.502, 613.8]),
            region("RULES", [675.444, 732.6, 789.48, 767.8], "SectionHeader"),
            region("Read the problem through carefully, considering every detail, then try to answer the questions at the end.", [343.57, 776.6, 1128.664, 877.8]),
        ]}
        sequence = [(126, i) for i in range(len(p["regions"]))]
        self.assertEqual(order_region_keys({126: p}, sequence), sequence)


if __name__ == "__main__":
    unittest.main()
