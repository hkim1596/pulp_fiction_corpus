"""A corrupt contents/folio digit cannot override a proven printed start."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


def region(text, y=100, label="Text"):
    return {"text": text, "label": label, "bbox": [80, y, 920, y + 65]}


def page(pn, regions):
    return {"width": 1000, "height": 1400, "page": pn,
            "regions": regions + [region(str(pn), 1330, "PageFooter")]}


class ContentsFolioEvidence(unittest.TestCase):
    def serial(self):
        pages = {76: page(76, [region("BY MARY AUTHOR"), region("Second of three parts. A dangerous adventure.", 250)]),
                 79: page(79, [region("THE SANCTUARY", 10, "PageHeader")])}
        entry = {"title": "The Sanctuary", "author": "Mary Author", "page": 76,
                 "type": "serial_part", "blurb": "Part Two of Three Parts"}
        return pages, 76, {"idx": 0, "author": "MARY AUTHOR"}, [entry], 0

    def test_byline_descriptor_and_running_title_recover_missing_title(self):
        args = self.serial()
        self.assertIs(rules.nearby_serial_contents(*args), args[3][0])

    def test_body_byline_cannot_recover_start(self):
        args = self.serial()
        args[0][76]["regions"].insert(0, region("Earlier narrative."))
        args[2]["idx"] = 1
        self.assertIsNone(rules.nearby_serial_contents(*args))

    def test_missing_serial_descriptor_is_insufficient(self):
        args = self.serial()
        args[0][76]["regions"][1]["text"] = "The author will tell you a dangerous story."
        self.assertIsNone(rules.nearby_serial_contents(*args))

    def test_wrong_running_title_is_insufficient(self):
        args = self.serial()
        args[0][79]["regions"][0]["text"] = "MAGAZINE"
        self.assertIsNone(rules.nearby_serial_contents(*args))

    def test_distant_contents_page_is_not_a_nearby_folio_error(self):
        args = self.serial()
        args[3][0]["page"] = 96
        self.assertIsNone(rules.nearby_serial_contents(*args))

    def test_wrong_author_is_not_a_matching_story(self):
        args = self.serial()
        args[2]["author"] = "Henry Writer"
        self.assertIsNone(rules.nearby_serial_contents(*args))

    def test_author_only_contents_uses_unique_printed_title_and_byline(self):
        entries = [{"title": "by Mary Author", "author": None, "page": 93, "type": "feature", "blurb": ""}]
        pages = {63: page(63, [region("THE TRAJECTORY", 100, "SectionHeader"), region("By Mary Author", 220),
                               region("He watched the stars while she stood beside him. " * 20, 350)]),
                 93: page(93, [region("a continuation of another story. " * 10)])}
        with patch.object(rules, "parse_toc", return_value=(copy.deepcopy(entries), None)):
            analysis = rules.analyse(pages)
        self.assertEqual(analysis["toc"][0]["title"], "by Mary Author")
        self.assertEqual(analysis["toc"][0]["author_only_printed_match"]["title"], "THE TRAJECTORY")
        self.assertEqual(analysis["toc"][0]["scan_linked"], 63)
        self.assertIsNone(analysis["pages"][63]["starts"][0]["toc"])
        self.assertEqual(analysis["pages"][93]["starts"], [])

    def test_department_heading_and_running_title_override_wrong_contents_folio(self):
        entries = [{"title": "BOOK REVIEW", "author": None, "page": 78, "type": "feature", "blurb": ""}]
        pages = {73: page(73, [region("BOOK REVIEW", 100, "SectionHeader"), region("A discussion of the novel. " * 20, 250),
                               region("BOOK REVIEW", 1300, "PageFooter")]),
                 78: page(78, [region("a continuation of another story. " * 10)])}
        with patch.object(rules, "parse_toc", return_value=(copy.deepcopy(entries), None)):
            analysis = rules.analyse(pages)
        self.assertEqual(analysis["pages"][73]["starts"][0]["toc"]["scan_linked"], 73)
        self.assertEqual(analysis["pages"][78]["starts"], [])


if __name__ == "__main__":
    unittest.main()
