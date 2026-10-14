"""Fictional testimony with a byline must stay inside its enclosing story."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


BODY = ("Slade watched the road while she stood beside him. They had travelled far "
        "and he knew that his friends were waiting beyond the forest. ") * 7
TITLE = "Statement to the Coroner's Jury"


def box(text, y=100, label="Text", h=100):
    return {"text": text, "label": label, "bbox": [100, y, 900, y + h], "order": y}


def page(n, rows):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": rows + [box("ASTOUNDING SCIENCE FICTION", 20, "PageHeader", 30),
                               box(str(n), 1330, "PageFooter", 30)]
                       + ([box("THE CHRONICLER", 1290, "PageFooter", 30)] if n % 2 else [])}


class EmbeddedDocuments(unittest.TestCase):
    def pages(self):
        return {
            1: page(1, [box("THE CHRONICLER", 100, "SectionHeader"),
                        box("By A. E. van Vogt", 230), box(BODY, 400, h=600)]),
            2: page(2, [box(BODY, 100, h=300),
                        box(TITLE + "\nBy John Alden, Farmer,\nSmailes County", 420, "SectionHeader"),
                        box(BODY, 600, h=600)]),
            3: page(3, [box(BODY, 100, h=900)]),
            4: page(4, [box(BODY, 100, h=300),
                        box(TITLE + "\nBy Detective Lieutenant\nJim Murphy", 420, "SectionHeader"),
                        box(BODY, 600, h=600)]),
            5: page(5, [box(BODY, 100, h=900)]),
        }

    def analyse(self, pages, entries=None):
        toc = [{"title": "THE CHRONICLER", "author": "A. E. van Vogt", "page": 1,
                "type": "serial_part", "blurb": "Part II"}]
        with patch.object(rules, "parse_toc", return_value=(copy.deepcopy(toc + (entries or [])), None)):
            return rules.analyse(pages)

    def test_repeated_signatories_under_same_running_story_title_are_headings(self):
        pages = self.pages()
        analysis = self.analyse(pages)
        for pn in (2, 4):
            self.assertEqual(analysis["pages"][pn]["starts"], [])
            self.assertEqual(analysis["pages"][pn]["embedded_document_headings"], [1])
        doc = rules.assemble("x", pages, analysis)
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["pages"], [1, 2, 3, 4, 5])
        self.assertEqual(stories[0]["roles"]["2:1"], "heading")
        self.assertIn("John Alden", stories[0]["text"])
        self.assertIn("Jim Murphy", stories[0]["text"])
        self.assertEqual(doc["checks"]["unassigned_regions"], 0)
        self.assertEqual(doc["checks"]["double_owned_regions"], 0)

    def test_single_documentary_title_remains_a_possible_real_story(self):
        pages = self.pages()
        pages[4]["regions"][1]["text"] = "A Different Story\nBy Jane Smith"
        self.assertEqual(self.analyse(pages)["pages"][2]["starts"][0]["title"], TITLE)

    def test_conflicting_running_head_prevents_suppression(self):
        pages = self.pages()
        pages[5]["regions"][-1]["text"] = "A DIFFERENT STORY"
        self.assertEqual(self.analyse(pages)["pages"][2]["starts"][0]["title"], TITLE)

    def test_contents_listed_documentary_work_remains_a_story(self):
        analysis = self.analyse(self.pages(), [{"title": TITLE, "author": "John Alden, Farmer, Smailes County",
                                               "page": 2, "type": "story", "blurb": ""}])
        self.assertEqual(analysis["pages"][2]["starts"][0]["title"], TITLE)

    def test_end_mark_before_statement_prevents_suppression(self):
        pages = self.pages()
        pages[2]["regions"].insert(1, box("THE END", 405, h=10))
        self.assertEqual(self.analyse(pages)["pages"][2]["starts"][0]["title"], TITLE)

    def test_same_author_repeated_title_is_not_a_witness_sequence(self):
        pages = self.pages()
        pages[4]["regions"][1]["text"] = pages[2]["regions"][1]["text"]
        self.assertEqual(self.analyse(pages)["pages"][2]["starts"][0]["title"], TITLE)


if __name__ == "__main__":
    unittest.main()
