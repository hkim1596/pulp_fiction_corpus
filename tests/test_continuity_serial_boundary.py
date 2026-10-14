import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "pipeline"), str(ROOT / "scripts")]
from continuity_serial_boundary import corrected_serial_end_prefix


class SerialBoundary(unittest.TestCase):
    def fixture(self):
        def r(text, label="Text"):
            return {"text": text, "label": label, "bbox": [0, 0, 400, 100]}
        pages = {
            1: {"regions": [r("My Story\nBy Mary Author", "SectionHeader"), r("Opening narrative.")]},
            2: {"regions": [r("Ending narrative."), r("TO BE CONTINUED NEXT WEEK. Don’t forget this magazine is issued weekly."), r("Other opening."), r("634", "PageFooter")]},
            3: {"regions": [r("Other Story", "PageHeader"), r("Other narrative.")]},
            4: {"regions": [r("OTHER STORY.", "PageHeader"), r("Other ending.")]},
        }
        for page in pages.values():
            page.update(width=1000, height=1400)
        pages[2]["regions"][3]["bbox"] = [900, 1320, 950, 1360]
        before = {"title": "My Story", "author": "Mary Author", "type": "story",
                  "serial": {"part_n": 3, "part_label": "Part Three"},
                  "work_id": "mag:my_story", "roles": {"1:0": "title"},
                  "fragments": [{"page": 1, "region_ids": [0, 1]}, {"page": 2, "region_ids": [0, 1, 2]},
                                {"page": 3, "region_ids": [1]}, {"page": 4, "region_ids": [1]}]}
        after = copy.deepcopy(before)
        after["fragments"] = [{"page": 1, "region_ids": [0, 1]}, {"page": 2, "region_ids": [0]}]
        following = {"type": "story", "title": "Other Story", "author": "Henry Writer",
                     "toc": {"title": "Other Story", "author": "Henry Writer", "page": 634, "type": "story"},
                     "fragments": [{"page": 2, "region_ids": [1, 2]}, {"page": 3, "region_ids": [1]},
                                   {"page": 4, "region_ids": [1]}]}
        return before, after, {"articles": [after, following]}, pages

    def test_explicit_end_contents_folio_and_repeated_heads_prove_prefix(self):
        self.assertTrue(corrected_serial_end_prefix(*self.fixture()))

    def test_same_issue_page_continuation_does_not_end_serial(self):
        args = self.fixture()
        args[3][2]["regions"][1]["text"] = "TO BE CONTINUED ON PAGE 640."
        self.assertFalse(corrected_serial_end_prefix(*args))

    def test_wrong_contents_folio_does_not_prove_boundary(self):
        args = self.fixture()
        args[2]["articles"][1]["toc"]["page"] = 635
        self.assertFalse(corrected_serial_end_prefix(*args))

    def test_one_running_head_is_not_enough(self):
        args = self.fixture()
        args[3][4]["regions"][0]["text"] = "MAGAZINE"
        self.assertFalse(corrected_serial_end_prefix(*args))

    def test_changed_serial_ordinal_is_rejected(self):
        args = self.fixture()
        args[1]["serial"]["part_n"] = 4
        self.assertFalse(corrected_serial_end_prefix(*args))

    def test_missing_reading_box_before_endpoint_is_not_a_prefix(self):
        args = self.fixture()
        args[1]["fragments"][0]["region_ids"].remove(1)
        self.assertFalse(corrected_serial_end_prefix(*args))

    def test_chapter_by_same_author_is_not_a_different_work(self):
        args = self.fixture()
        following = args[2]["articles"][1]
        following["author"] = following["toc"]["author"] = "Mary Author"
        self.assertFalse(corrected_serial_end_prefix(*args))

    def test_missing_own_printed_identity_is_rejected(self):
        args = self.fixture()
        args[3][1]["regions"][0]["text"] = "CHAPTER III"
        self.assertFalse(corrected_serial_end_prefix(*args))


if __name__ == "__main__":
    unittest.main()
