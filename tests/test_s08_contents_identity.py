"""A contents label must identify the actual printed story start."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules

PROSE = ("He watched the road while she stood beside him. They had travelled far "
         "and he knew that his friends were waiting beyond the forest. ") * 12


def region(text, y, label="Text"):
    return {"text": text, "label": label, "bbox": [80, y, 920, y + 70], "order": y}


def page(regions):
    return {"page": 7, "width": 1000, "height": 1400,
            "regions": regions + [region("7", 1330, "PageFooter")]}


def entry(title, author=None, pn=7):
    return {"title": title, "author": author, "page": pn, "type": "story", "blurb": ""}


class ContentsIdentity(unittest.TestCase):
    def analyse(self, entries, regions):
        with patch.object(rules, "parse_toc", return_value=(copy.deepcopy(entries), None)):
            return rules.analyse({7: page(regions)})

    def normal_regions(self, title="THE CHRONICLER", author="A. E. VAN VOGT"):
        return [region(title, 100, "SectionHeader"), region("By " + author, 210), region(PROSE, 350)]

    def test_inline_contents_byline_is_separated_and_claims_printed_start(self):
        analysis = self.analyse([entry("THE CHRONICLER, by A. E. van Vogt"),
                                 entry("THE ATOMIC PILE", pn=89)], self.normal_regions())
        start = analysis["pages"][7]["starts"][0]
        self.assertEqual(start["toc"]["title"], "THE CHRONICLER")
        self.assertEqual(start["toc"]["author"], "A. E. van Vogt")
        self.assertEqual(start["toc"]["title_as_read"], "THE CHRONICLER, by A. E. van Vogt")
        self.assertEqual(len(analysis["pages"][7]["starts"]), 1)

    def test_shared_letter_shapes_do_not_link_unrelated_title(self):
        analysis = self.analyse([entry("THE ATOMIC PILE", pn=89)], self.normal_regions())
        self.assertIsNone(analysis["pages"][7]["starts"][0]["toc"])
        self.assertNotIn("scan_linked", analysis["toc"][0])

    def test_exact_title_beats_earlier_cover_credit_with_same_author(self):
        analysis = self.analyse([entry("H. W. WESSO", "R. F. STARZL", 1),
                                 entry("IN THE ORBIT OF SATURN", "R. F. STARZL", 1)],
                                self.normal_regions("In the Orbit of Saturn", "R. F. Starzl"))
        self.assertEqual(analysis["pages"][7]["starts"][0]["toc"]["title"], "IN THE ORBIT OF SATURN")
        self.assertNotIn("scan_linked", analysis["toc"][0])

    def test_serial_subtitle_does_not_hide_the_heading(self):
        analysis = self.analyse([], [region("Brood of the Dark Moon", 100, "SectionHeader"),
                                   region("Part Three of a Four-Part Novel", 200),
                                   region("By Charles Willard Diffin", 310), region(PROSE, 430)])
        self.assertEqual(analysis["pages"][7]["starts"][0]["title"], "Brood of the Dark Moon")

    def test_combined_printed_heading_and_byline_is_a_start(self):
        analysis = self.analyse([], [region("The Heads of Apex\nBy Francis Flagg", 100, "SectionHeader"),
                                   region(PROSE, 300)])
        start = analysis["pages"][7]["starts"][0]
        self.assertEqual((start["title"], start["author"]), ("The Heads of Apex", "Francis Flagg"))

    def test_body_mention_of_byline_is_not_a_heading(self):
        analysis = self.analyse([], [region("The Heads of Apex\nBy Francis Flagg", 100, "Text"),
                                   region(PROSE, 300)])
        self.assertEqual(analysis["pages"][7]["starts"], [])

    def test_standalone_by_header_still_uses_next_name_box(self):
        analysis = self.analyse([], [region("The Road", 100, "SectionHeader"),
                                   region("By", 210, "SectionHeader"), region("Jane Smith", 290),
                                   region(PROSE, 430)])
        self.assertEqual(analysis["pages"][7]["starts"][0]["author"], "Jane Smith")


if __name__ == "__main__":
    unittest.main()
