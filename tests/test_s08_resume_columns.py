"""A short dialogue or adjacent advertisement cannot strand a story tail."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules

PROSE = ("He watched the road while she stood beside him. They had travelled far "
         "and he knew that his friends were waiting beyond the forest. ") * 3


def region(text, box, label="Text"):
    return {"text": text, "label": label, "bbox": box}


class ResumeColumns(unittest.TestCase):
    def blocked(self, regs):
        return rules.generic_resume_blocker({"width": 1000, "height": 1400, "regions": regs},
                                            {"starts": [], "furniture": [], "chapters": []})

    def test_short_dialogue_before_long_paragraph_is_not_a_service(self):
        first = '"And now may I ask," said he, "what you intend to do about the missing treasure?"'
        self.assertFalse(rules.prose_like(region(first, [0, 0, 400, 100])))
        self.assertIsNone(self.blocked([region(first, [0, 0, 400, 100]), region(PROSE, [0, 100, 400, 800])]))

    def test_descriptive_opening_without_pronouns_can_resume(self):
        first = "The car began to move. It slid into the air lock with a clang. Water gurgled around it. Then—acceleration!"
        self.assertFalse(rules.prose_like(region(first, [0, 0, 400, 100])))
        self.assertIsNone(self.blocked([region(first, [0, 0, 400, 100]), region(PROSE, [0, 100, 400, 800])]))

    def test_first_person_dialogue_can_resume(self):
        first = '"I can explain this, but first you must listen to everything I intend to say about the flight."'
        self.assertIsNone(self.blocked([region(first, [0, 0, 400, 100]), region(PROSE, [0, 100, 400, 800])]))

    def test_coupon_in_other_column_does_not_block_story(self):
        self.assertIsNone(self.blocked([region("Send today for a FREE trial. Mail coupon now.", [0, 100, 400, 600]),
                                       region(PROSE, [550, 100, 1000, 700])]))

    def test_full_width_service_header_blocks_both_columns(self):
        self.assertIsNotNone(self.blocked([
            region("A Free Question and Answer Service Bureau of Information", [0, 0, 1000, 100], "PageHeader"),
            region(PROSE, [550, 200, 1000, 700])]))

    def test_direct_offer_before_prose_in_same_column_still_blocks(self):
        self.assertIsNotNone(self.blocked([region("Mail coupon now. Money refunded if not satisfied.", [0, 0, 400, 100]),
                                          region(PROSE, [0, 120, 400, 700])]))

    def test_ad_below_story_is_not_its_opening_even_if_ocr_lists_it_first(self):
        self.assertIsNone(self.blocked([region("Mail coupon now. Money refunded.", [0, 1000, 400, 1300]),
                                       region(PROSE, [0, 100, 400, 700])]))


if __name__ == "__main__":
    unittest.main()
