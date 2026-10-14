"""Source-backed regressions: Dime Detective's facing title and ad-side continuation."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


BODY = ("HE WAS waiting beside the road while she watched the old house. "
        "They had come a long way together and neither wanted to leave before "
        "the others reached home. ") * 7


def box(text, y=100, label="Text", x0=100, x1=900, h=100):
    return {"text": text, "label": label, "bbox": [x0, y, x1, y + h], "order": y}


def page(n, rows):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": rows + [box(str(n), 1320, "PageFooter", h=30)]}


class FacingAndContinuation(unittest.TestCase):
    def facing(self, extra=None, title="THE MERRY WIVES OF MURDER"):
        return {1: page(1, [box(title, 300, "SectionHeader", h=300)] + (extra or [])),
                2: page(2, [box("Exciting Crime-Adventure Novelette", 100),
                            box("By\nTIAH DEVITT", 250), box(BODY, 450, h=700)])}

    def test_title_only_facing_page_and_byline_open_real_story(self):
        pages = self.facing()
        doc = rules.assemble("x", pages, rules.analyse(pages))
        story = next(r for r in doc["articles"] if r.get("title") == "The Merry Wives of Murder")
        self.assertEqual(story["type"], "story")
        self.assertEqual(story["pages"], [1, 2])
        self.assertEqual(story["author"], "TIAH DEVITT")
        self.assertEqual(story["roles"]["1:0"], "title")
        self.assertIn("neither wanted to leave", story["text"])
        self.assertEqual(doc["checks"]["double_owned_regions"], 0)

    def test_prior_story_or_advertising_title_cannot_supply_facing_start(self):
        for pages in (self.facing([box(BODY, 650, h=500)]), self.facing(title="SEND FOR FREE BOOK")):
            with self.subTest(title=pages[1]["regions"][0]["text"]):
                a = rules.analyse(pages)
                self.assertFalse(any(s.get("facing_title") for s in a["pages"][2]["starts"]))

    def test_conflicting_contents_author_blocks_facing_join(self):
        pages = self.facing()
        entry = {"title": "THE MERRY WIVES OF MURDER", "author": "Different Writer", "page": 1,
                 "type": "story", "blurb": ""}
        with patch.object(rules, "parse_toc", return_value=([entry], None)):
            a = rules.analyse(pages)
        self.assertFalse(any(s.get("facing_title") for s in a["pages"][2]["starts"]))

    def test_inline_continuation_after_ad_preserves_body_and_distinct_ads(self):
        pages = {1: page(1, [box("THE OLD ROAD", 100, "SectionHeader"),
                            box("By Jane Smith", 250), box(BODY, 400, h=700)]),
                 2: page(2, [box("FREE BOOK", 100, "SectionHeader", 50, 450),
                            box("Send now to Example Company, Dept. 2, Chicago. Free offer.", 220, x0=50, x1=450),
                            box("LAW TRAINING", 400, "SectionHeader", 50, 450),
                            box("Free books. Send to School Company, Dept. 5, Chicago.", 520, x0=50, x1=450),
                            box("Jane Smith", 100, "SectionHeader", 550, 950, 30),
                            box("(Continued from page 1)\n\nhe saw his friends waiting beside the door after the long journey.",
                                220, x0=550, x1=950),
                            box(BODY, 500, x0=550, x1=950, h=700)])}
        a = rules.analyse(pages)
        self.assertIn((5, 1), a["pages"][2]["cont_from"])
        self.assertNotIn(5, a["pages"][2]["furniture"])
        doc = rules.assemble("x", pages, a)
        story = next(r for r in doc["articles"] if r.get("title") == "The Old Road")
        self.assertIn("he saw his friends waiting", story["text"])
        keys = {f"{f['page']}:{i}" for f in story["fragments"] for i in f["region_ids"]}
        self.assertIn("2:5", keys)
        self.assertNotIn("2:3", keys)
        self.assertEqual(doc["checks"]["unassigned_regions"], 0)
        self.assertEqual(doc["checks"]["double_owned_regions"], 0)

    def test_foreign_prefixed_inline_notice_is_not_a_continuation(self):
        p = page(1, [box("Classified advertising continued from page 9\n\n" + BODY)])
        self.assertEqual(rules.analyse({1: p})["pages"][1]["cont_from"], [])


if __name__ == "__main__":
    unittest.main()
