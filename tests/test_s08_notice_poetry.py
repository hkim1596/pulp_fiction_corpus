"""Named continuation notices and short poems keep their correct reading owner."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


BODY = ("He watched the road while she stood beside him. They had travelled far "
        "and he knew that his friends were waiting beyond the forest. ") * 3
VERSE = "A BIRDIE built a tiny nest\nIn our old apple tree,\nI wonder if he is the one\nTells mother tales of me."


def region(text, y, label="Text"):
    return {"text": text, "label": label, "bbox": [100, y, 900, y + 100], "order": y}


def page(n, rows):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": rows + [region(str(n), 1300, "PageFooter")]}


def start(n, title):
    return page(n, [region(title, 100, "SectionHeader"), region("By Jane Smith", 240), region(BODY, 400)])


class NamedNotices(unittest.TestCase):
    def assemble(self, notice):
        pages = {1: start(1, "THE OLD ROAD"), 2: start(2, "THE NEW ROAD"),
                 3: page(3, [region(BODY, 100)]), 4: page(4, [region(BODY, 100)])}
        pages[1]["regions"].insert(-1, region(notice, 1000))
        doc = rules.assemble("fixture", pages, rules.analyse(pages))
        self.assertEqual(doc["checks"]["unassigned_regions"], 0)
        self.assertEqual(doc["checks"]["double_owned_regions"], 0)
        return {r["title"]: r for r in doc["articles"]}

    def test_foreign_named_advert_notice_cannot_steal_later_story(self):
        records = self.assemble("Classified Advertising continued on page 4.")
        self.assertEqual(records["The Old Road"]["pages"], [1])
        self.assertEqual(records["The New Road"]["pages"], [2, 3, 4])

    def test_matching_named_story_notice_still_resumes(self):
        records = self.assemble("The Old Road continued on page 4.")
        self.assertEqual(records["The Old Road"]["pages"], [1, 4])
        self.assertEqual(records["The New Road"]["pages"], [2, 3])

    def test_unnamed_parenthesized_notice_still_resumes(self):
        records = self.assemble("(Continued on page 4)")
        self.assertEqual(records["The Old Road"]["pages"], [1, 4])

    def test_foreign_named_next_page_notice_is_not_scheduled(self):
        pages = {1: start(1, "THE OLD ROAD"), 2: page(2, [region(BODY, 100)])}
        pages[1]["regions"].insert(-1, region("Classified Advertising continued on next page.", 1000))
        analysis = rules.analyse(pages)
        asm = rules.Assembler("fixture", pages, analysis)
        asm.story_page(1, pages[1], analysis["pages"][1])
        self.assertFalse(asm.continuations)

    def test_matching_named_next_page_notice_is_scheduled(self):
        pages = {1: start(1, "THE OLD ROAD"), 2: page(2, [region(BODY, 100)])}
        pages[1]["regions"].insert(-1, region("The Old Road continued on next page.", 1000))
        analysis = rules.analyse(pages)
        asm = rules.Assembler("fixture", pages, analysis)
        asm.story_page(1, pages[1], analysis["pages"][1])
        self.assertEqual(asm.continuations[2]["title"], "The Old Road")


class PoetryBody(unittest.TestCase):
    def test_poetry_section_outranks_unrelated_serial_announcement(self):
        toc = page(1, [region("CONTENTS", 50, "SectionHeader"),
                       region("POETRY\nTHE LITTLE BIRD ..... 2\nby Margaret G. Hays\n"
                              "A serial beginning next week.", 200, "TableOfContents")])
        entries, _ = rules.parse_toc({1: toc})
        self.assertEqual(entries[0]["section"], "POETRY")
        self.assertEqual(entries[0]["type"], "poem")

    def test_short_centred_verse_is_not_teaser(self):
        p = page(1, [region("THE LITTLE BIRD", 100, "SectionHeader"),
                     region(VERSE, 300), region("Margaret G. Hays.\n10 A", 500)])
        a = rules.analyse({1: p})
        a["pages"][1]["starts"] = [{"title": "The Little Bird", "author": "Margaret G. Hays",
            "title_idx": [0], "subtitle_idx": [], "teaser_idx": [], "byline_idx": None,
            "at": None, "toc": {"title": "The Little Bird", "author": "Margaret G. Hays",
                                  "page": 1, "type": "poem", "blurb": "A serial beginning next week."}}]
        doc = rules.assemble("fixture", {1: p}, a)
        poem = next(r for r in doc["articles"] if r.get("title") == "The Little Bird")
        self.assertEqual(poem["type"], "poem")
        self.assertEqual(poem["text"], " ".join(VERSE.split()))
        self.assertNotIn("teaser", poem["roles"].values())
        self.assertNotIn("serial", poem)


if __name__ == "__main__":
    unittest.main()
