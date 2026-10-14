"""An ended story cannot own the next department's continuation notice."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


PROSE = ("He watched the road while she stood beside him. They had travelled far "
         "and he knew that his friends were waiting beyond the forest. ") * 3
INTRO = ("In this department we shall discuss topics of interest to readers. "
         "The editors invite correspondence on all subjects related to the stories.")


def region(text, y, label="Text"):
    return {"text": text, "label": label, "bbox": [80, y, 920, y + 70], "order": y}


def page(n, regs):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": regs + [region(str(n), 1330, "PageFooter")]}


class DepartmentBoundary(unittest.TestCase):
    def assemble(self, header="DISCUSSIONS", ended=True, intro=INTRO):
        pages = {
            1: page(1, [region("THE LONG ROAD", 100, "SectionHeader"),
                        region("By Jane Smith", 200), region(PROSE, 350)]),
            2: page(2, [region(PROSE, 100), region("THE END." if ended else "He waited.", 220),
                        region(header, 340, "SectionHeader"), region(intro, 470),
                        region("Editor, AMAZING STORIES:", 600), region(PROSE, 730),
                        region("(Continued on page 4)", 1100)]),
            3: page(3, [region("LEARN AT HOME", 100, "SectionHeader"),
                        region("Free coupon catalog. Send for free catalog. Write for free coupon. "
                               "Mail $1 postpaid. Address Dept. 7. Money back guarantee.", 300)]),
            4: page(4, [region(PROSE, 100)]),
        }
        analysis = rules.analyse(pages)
        analysis["magazine"] = "Amazing Stories"
        out = rules.assemble("fixture", pages, analysis)
        self.assertEqual(out["checks"]["unassigned_regions"], 0)
        self.assertEqual(out["checks"]["double_owned_regions"], 0)
        return out["articles"]

    def test_letters_own_forward_notice_after_story_end(self):
        articles = self.assemble()
        story = next(r for r in articles if r.get("title") == "The Long Road")
        letters = next(r for r in articles if r["type"] == "letters")
        self.assertEqual(story["pages"], [1, 2])
        self.assertEqual(letters["pages"], [2, 4])
        self.assertNotIn(INTRO, story["text"])
        self.assertIn(3, next(f for f in letters["fragments"] if f["page"] == 2)["region_ids"])

    def test_unreadable_heading_uses_explicit_editorial_intro(self):
        articles = self.assemble(header="")
        story = next(r for r in articles if r.get("title") == "The Long Road")
        letters = next(r for r in articles if any("department after" in f for f in r["flags"]))
        self.assertEqual(story["pages"], [1, 2])
        self.assertEqual(letters["pages"], [2, 4])
        self.assertIsNone(letters["title"])
        self.assertIn(INTRO, letters["text"])

    def test_discussions_without_story_end_does_not_make_new_record(self):
        articles = self.assemble(ended=False)
        self.assertFalse(any(r["type"] == "letters" for r in articles))
        story = next(r for r in articles if r.get("title") == "The Long Road")
        self.assertIn(4, story["pages"])

    def test_chapter_after_endmark_does_not_make_department(self):
        articles = self.assemble(header="CHAPTER II", intro=PROSE)
        story = next(r for r in articles if r.get("title") == "The Long Road")
        self.assertFalse(any("department after" in f for r in articles for f in r["flags"]))
        self.assertIn("2:2", story["roles"])
        self.assertEqual(story["roles"]["2:2"], "chapter")
        self.assertIn(4, story["pages"])

    def test_house_notice_after_endmark_is_not_owned_by_story(self):
        articles = self.assemble(header="Coming Next Month", intro="Another tale is coming next month.")
        story = next(r for r in articles if r.get("title") == "The Long Road")
        self.assertEqual(story["pages"], [1, 2])
        self.assertNotIn("Another tale", story["text"])


if __name__ == "__main__":
    unittest.main()
