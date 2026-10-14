import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "pipeline"), str(ROOT / "scripts")]
from continuity_metadata_splits import corrected_story_prefix


def region(text, label="Text"):
    return {"text": text, "label": label}


def record(title, author, pn):
    return {"type": "story", "title": title, "author": author,
            "fragments": [{"page": pn, "region_ids": [0, 1, 2]}],
            "roles": {f"{pn}:0": "title", f"{pn}:1": "author"}}


class CorrectedStoryPrefix(unittest.TestCase):
    def setUp(self):
        self.first = record("Lost Diggings", "Jane Smith", 1)
        self.second = record("The End of the Trail", "John Brown", 2)
        self.old = copy.deepcopy(self.first)
        self.old["fragments"] += copy.deepcopy(self.second["fragments"])
        self.old.update(work_id="true-link", serial={"part_n": 1, "next": {"issue": "next"}})
        self.candidate = {"articles": [self.first, self.second], "furniture": []}
        self.pages = {
            1: {"regions": [region("LOST DIGGINGS", "SectionHeader"), region("By Jane Smith"), region("First story.")]},
            2: {"regions": [region("The End of the Trail", "SectionHeader"), region("By John Brown"), region("Second story.")]},
        }

    def check(self):
        return corrected_story_prefix(self.old, self.first, self.candidate, self.pages)

    def test_later_independent_printed_work_can_leave_serial_prefix(self):
        self.assertTrue(self.check())
        self.assertEqual(self.old["serial"]["next"], {"issue": "next"})

    def test_embedded_title_and_byline_can_supply_independent_evidence(self):
        self.pages[2]["regions"][0]["text"] = "The End of the Trail\nBy John Brown"
        del self.second["roles"]["2:1"]
        self.assertTrue(self.check())

    def test_metadata_title_without_printed_heading_cannot_authorize_split(self):
        self.pages[2]["regions"][0] = region("He was still travelling.")
        self.assertFalse(self.check())

    def test_chapter_and_repeated_byline_cannot_authorize_split(self):
        self.second["title"] = "CHAPTER II"
        self.pages[2]["regions"][0]["text"] = "CHAPTER II"
        self.assertFalse(self.check())

    def test_unprinted_author_cannot_authorize_split(self):
        self.pages[2]["regions"][1]["text"] = "He returned."
        self.assertFalse(self.check())

    def test_unaccounted_tail_cannot_authorize_split(self):
        self.second["fragments"][0]["region_ids"].remove(2)
        self.assertFalse(self.check())

    def test_later_resumption_of_original_story_rejects_prefix_proof(self):
        self.first["fragments"].append({"page": 3, "region_ids": [0]})
        self.old["fragments"].append({"page": 3, "region_ids": [0]})
        self.assertFalse(self.check())

    def test_ad_absorption_of_real_story_tail_is_rejected(self):
        self.second["type"] = "ad"
        self.assertFalse(self.check())

    def test_literal_contents_chapter_range_and_serial_length_are_not_story_title(self):
        self.first["title"] = self.old["title"] = "CHAPTERS XI-XVIA MIRACLE OF FAITH. In Five Parts."
        self.pages[1]["regions"][0]["text"] = "A MIRACLE OF FAITH"
        self.assertTrue(self.check())
        self.pages[1]["regions"][0]["text"] = "DIFFERENT DIGGINGS"
        self.assertFalse(self.check())

    def test_exact_combined_header_without_space_before_by_is_printed_identity(self):
        self.pages[1]["regions"][0]["text"] = "Lost Diggingsby Jane Smith"
        self.first["roles"] = {}
        self.assertTrue(self.check())
        self.pages[1]["regions"][0]["text"] = "Lost Diggingsby John Smith"
        self.assertFalse(self.check())

    def test_only_short_opening_chapter_apparatus_may_be_added_to_prefix(self):
        self.pages[1]["regions"].append(region("CHAPTER III", "SectionHeader"))
        self.first["fragments"][0]["region_ids"].append(3)
        self.first["roles"]["1:3"] = "chapter"
        self.assertTrue(self.check())
        self.first["roles"].pop("1:3")
        self.pages[1]["regions"][3] = region("Actual newly inserted prose.")
        self.assertFalse(self.check())


if __name__ == "__main__":
    unittest.main()
