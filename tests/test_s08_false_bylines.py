"""Editorial idioms and future-issue advertisements are not author starts."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


BODY = ("He watched the road while she stood beside him. They had travelled far "
        "and he knew that his friends were waiting beyond the forest. ") * 12


def box(text, y=100, label="Text", h=100):
    return {"text": text, "label": label, "bbox": [100, y, 900, y + h], "order": y}


def page(n, rows):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": rows + [box(str(n), 1330, "PageFooter", 30)]}


class FalseBylines(unittest.TestCase):
    def test_by_way_of_preface_does_not_replace_actual_author(self):
        p = page(1, [box("TUNED OUT", 100, "SectionHeader"), box("By RUFUS KING", 220),
                     box('Author of "Dirty Work," etc.', 320),
                     box("BY WAY OF PREFACE.", 430, "SectionHeader"), box(BODY, 550, h=650)])
        analysis = rules.analyse({1: p})
        self.assertEqual([(s["title"], s["author"]) for s in analysis["pages"][1]["starts"]],
                         [("TUNED OUT", "RUFUS KING")])
        doc = rules.assemble("x", {1: p}, analysis)
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["author"], "RUFUS KING")
        self.assertIn("friends were waiting", stories[0]["text"])

    def test_real_author_whose_name_begins_with_way_is_kept(self):
        self.assertEqual(rules.byline_author("By Wayne King"), "Wayne King")
        self.assertEqual(rules.byline_author("By Wayland Smith"), "Wayland Smith")

    def test_collective_author_advertising_imperative_is_not_a_person(self):
        self.assertIsNone(rules.byline_author("by Famous Authors Read a POPULAR PUBLICATION"))
        p = page(1, [box("For Sure-Fire Stories\nby Famous Authors Read a\nPOPULAR PUBLICATION",
                         100, "SectionHeader"), box(BODY, 350, h=900)])
        self.assertEqual(rules.analyse({1: p})["pages"][1]["starts"], [])

    def test_surnames_read_and_author_are_not_advertising_phrases(self):
        self.assertEqual(rules.byline_author("By Herbert Read"), "Herbert Read")
        self.assertEqual(rules.byline_author("By Frank Author"), "Frank Author")

    def commercial_page(self):
        return page(1, [box("Three little words\nby Noah Webster", 100, "SectionHeader"),
                        box("Dry means lacking sweetness. Enjoy the fine flavor of our whiskey with your friends. " * 8,
                            350, h=600),
                        box("This advertisement offers this product for sale only in compliance with local statutes.",
                            1200, h=40)])

    def test_author_quote_on_self_declared_commercial_page_is_not_a_story(self):
        analysis = rules.analyse({1: self.commercial_page()})
        self.assertEqual(analysis["pages"][1]["starts"], [])
        self.assertIn("advertisement", analysis["pages"][1]["rejected_starts"][0]["rejected"])

    def test_story_beside_ad_disclaimer_keeps_its_byline(self):
        p = self.commercial_page()
        p["regions"][1]["text"] = BODY
        self.assertEqual(rules.analyse({1: p})["pages"][1]["starts"][0]["author"], "Noah Webster")

    def test_small_column_disclaimer_cannot_identify_whole_page_as_ad(self):
        p = self.commercial_page()
        p["regions"][2]["bbox"] = [600, 1200, 950, 1240]
        self.assertTrue(rules.analyse({1: p})["pages"][1]["starts"])

    def test_embedded_future_issue_byline_remains_an_ad_despite_following_body(self):
        p = page(1, [box("THE OLD ROAD", 100, "SectionHeader"), box("By Jane Smith", 220),
                     box(BODY, 350, h=400),
                     box("Next Issue's Novel: DEEP IN THE HELL OF DIEPPE, by Laurence Donovan",
                         850, "SectionHeader"), box("Shaving blades, four for ten cents.", 1000)])
        pages = {1: p, 2: page(2, [box(BODY, 100, h=900)])}
        analysis = rules.analyse(pages)
        self.assertFalse(any("Next Issue" in s["title"] for s in analysis["pages"][1]["starts"]))
        self.assertIn("future-issue", analysis["pages"][1]["rejected_starts"][0]["rejected"])
        doc = rules.assemble("x", pages, analysis)
        ad = next(r for r in doc["articles"] if "Next Issue" in (r.get("title") or ""))
        self.assertIn(ad["type"], ("ad", "house"))
        self.assertEqual(ad["pages"], [1])
        self.assertEqual(doc["checks"]["unassigned_regions"], 0)
        self.assertEqual(doc["checks"]["double_owned_regions"], 0)

    def test_title_about_next_week_is_still_a_story(self):
        p = page(1, [box("Next Week's Promise\nBy Jane Smith", 100, "SectionHeader"),
                     box(BODY, 350, h=900)])
        self.assertEqual(rules.analyse({1: p})["pages"][1]["starts"][0]["title"], "Next Week's Promise")


if __name__ == "__main__":
    unittest.main()
