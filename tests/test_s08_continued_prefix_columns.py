"""A later continued-from heading must not consume the preceding story's columns."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


PROSE = ("He watched the road while she stood beside him. They had travelled far "
         "and he knew that his friends were waiting beyond the forest. ") * 2


def region(text, box, label="Text"):
    return {"text": text, "bbox": box, "label": label, "order": 0}


def page(n, regs):
    return {"page": n, "width": 1500, "height": 2000,
            "regions": regs + [region(str(n), [50, 1950, 100, 1980], "PageFooter")]}


class ContinuedPrefixColumns(unittest.TestCase):
    def fixture(self):
        pages = {
            1: page(1, [region("THE OLDER TALE", [400, 100, 1100, 180], "SectionHeader"),
                        region("By Ann Smith", [400, 200, 1100, 250]),
                        region(PROSE, [400, 300, 1100, 800])]),
            2: page(2, [region("THE LONG ROAD", [400, 100, 1100, 180], "SectionHeader"),
                        region("By Jane Brown", [400, 200, 1100, 250]),
                        region(PROSE + "The effect of his answer was", [400, 300, 1100, 800])]),
            3: page(3, [region("TRAVEL BY TRAIN", [50, 100, 450, 180], "SectionHeader"),
                        region("Mail coupon for free fares. Send $1 to Dept. 5, Chicago, Ill.", [50, 200, 450, 650]),
                        region("Name _____ Address _____", [50, 700, 450, 750], "Form"),
                        region("startling. " + PROSE, [500, 100, 940, 600]),
                        region('"Why?"', [500, 620, 940, 670]),
                        region(PROSE, [500, 690, 940, 1100]),
                        region("a black band around his arm. " + PROSE, [1000, 100, 1440, 600]),
                        region('"Go ahead," he said.', [1000, 620, 1440, 670]),
                        region("THE OLDER TALE (Continued from page 1)", [500, 1200, 1440, 1250], "SectionHeader"),
                        region(PROSE, [500, 1300, 1440, 1800])]),
        }
        analysis = rules.analyse(pages)
        return pages, analysis

    def test_full_assembly_retains_short_dialogue_and_separate_ad_column(self):
        pages, analysis = self.fixture()
        result = rules.assemble("fixture", pages, analysis)
        road = next(r for r in result["articles"] if r["title"] == "The Long Road")
        older = next(r for r in result["articles"] if r["title"] == "The Older Tale")
        keys = {(f["page"], i) for f in road["fragments"] for i in f["region_ids"]}
        self.assertTrue({(3, i) for i in range(3, 8)} <= keys)
        self.assertFalse({(3, i) for i in range(3)} & keys)
        self.assertNotIn((3, 9), keys)
        self.assertIn(3, older["pages"])
        self.assertEqual(result["checks"]["double_owned_regions"], 0)
        self.assertEqual(result["checks"]["unassigned_regions"], 0)

    def call(self, mutate=None, gap=False, target_is_open=False):
        pages, analysis = self.fixture()
        asm = rules.Assembler("fixture", pages, analysis)
        old = asm.new("story", title="The Long Road", page=2)
        asm.add(old, 1 if gap else 2, 2)
        asm.open = old
        asm.last_text = "The effect of his answer was"
        target = old if target_is_open else asm.new("story", title="The Older Tale", page=1)
        if mutate:
            mutate(pages[3], analysis["pages"][3])
        return asm.continued_prefix_columns(3, pages[3], analysis["pages"][3], list(range(8)), 8, target)

    def test_no_immediate_preceding_story_does_not_infer_identity(self):
        self.assertEqual(self.call(gap=True), [])

    def test_same_named_target_does_not_use_previous_story_rule(self):
        self.assertEqual(self.call(target_is_open=True), [])

    def test_new_capitalized_opening_is_not_assumed_to_continue(self):
        def change(page, _):
            page["regions"][3]["text"] = "Startling. " + PROSE
        self.assertEqual(self.call(change), [])

    def test_advertising_column_is_required(self):
        def change(page, info):
            info["fillers"] = []
            info["forms"] = {}
            for i in range(3):
                page["regions"][i]["text"] = "ornament"
                page["regions"][i]["label"] = "Caption"
        self.assertEqual(self.call(change), [])

    def test_single_paragraph_does_not_supply_multiple_column_evidence(self):
        def change(page, _):
            page["regions"][5]["text"] = '"No."'
            page["regions"][6]["text"] = '"No."'
        self.assertEqual(self.call(change), [])

    def test_no_story_boxes_below_the_later_heading_are_claimed(self):
        def change(page, _):
            page["regions"][5]["bbox"] = [500, 1300, 940, 1600]
        self.assertEqual(self.call(change), [])

    def test_prose_like_advertisement_cannot_supply_narrative_column(self):
        def change(page, _):
            page["regions"][3]["text"] = "before he knew it, he had found success. " + PROSE
            page["regions"][4]["text"] = "Send no money. Your money refunded after the free trial."
        kept = self.call(change)
        self.assertFalse(set(range(3, 6)) & set(kept))

    def previous_ending(self, ending, stale="An unrelated record was"):
        pages, analysis = self.fixture()
        asm = rules.Assembler("fixture", pages, analysis)
        old = asm.new("story", title="The Long Road", page=2)
        asm.add(old, 2, 2)
        asm.open = old
        pages[2]["regions"][2]["text"] = ending
        asm.last_text = stale
        return asm.continued_prefix_columns(3, pages[3], analysis["pages"][3], list(range(8)), 8, None)

    def test_completed_previous_text_does_not_supply_run_on_identity(self):
        self.assertEqual(self.previous_ending("He had reached the end."), [])
        self.assertEqual(self.previous_ending('"No."'), [])

    def test_explicit_ending_or_continuation_notice_does_not_supply_run_on_identity(self):
        for ending in ("THE END", "(Continued on page 8)", "TO BE CONTINUED"):
            with self.subTest(ending=ending):
                self.assertEqual(self.previous_ending(ending), [])

    def test_semicolon_run_on_can_continue(self):
        self.assertTrue(self.previous_ending("She lay down under the trees and closed her eyes. Heaven;"))

    def test_stale_last_text_cannot_change_owned_reading_evidence(self):
        self.assertFalse(self.previous_ending("He had reached the end.", stale="Still unfinished"))
        self.assertTrue(self.previous_ending("He knew that the effect of his long answer was", stale="THE END"))

    def test_owned_end_note_overrides_unfinished_reading_text(self):
        for ending in ("THE END", "TO BE CONTINUED"):
            with self.subTest(ending=ending):
                pages, analysis = self.fixture()
                pages[2]["regions"].append(region(ending, [400, 900, 1100, 950]))
                asm = rules.Assembler("fixture", pages, analysis)
                old = asm.new("story", title="The Long Road", page=2)
                asm.add(old, 2, 2)
                asm.add(old, 2, len(pages[2]["regions"]) - 1, "note")
                asm.open = old
                self.assertEqual(asm.continued_prefix_columns(3, pages[3], analysis["pages"][3],
                                                             list(range(8)), 8, None), [])

    def test_prize_money_does_not_make_a_narrative_column_an_advertisement(self):
        def change(page, _):
            page["regions"][6]["text"] = "and he won $16,000 in prizes, with $1500 for his laps. " + PROSE
        self.assertEqual(self.call(change), list(range(3, 8)))

    def test_inset_coupon_does_not_leave_short_advertising_line_in_story(self):
        def change(page, info):
            page["regions"][0] = region("There is only one", [50, 100, 150, 150])
            page["regions"][1] = region("THE EXPRESS", [50, 200, 450, 650], "SectionHeader")
            page["regions"][2] = region("Mail coupon", [300, 700, 420, 750])
            info["forms"] = {}
        self.assertEqual(self.call(change), list(range(3, 8)))

    def test_technical_continuation_column_needs_no_pronoun_quota(self):
        def change(page, _):
            page["regions"][5]["text"] = PROSE + "The rear gunner is"
            page["regions"][6]["text"] = "armed with a four-gun battery in a movable tail cone. Both men have retractable sights."
            page["regions"][7]["text"] = "Flight controls of the aircraft are similar to those of the earlier model."
        self.assertEqual(self.call(change), list(range(3, 8)))

    def test_bare_money_and_filler_heading_do_not_supply_commercial_evidence(self):
        def change(page, info):
            page["regions"][0]["text"] = "The Baseball Season"
            page["regions"][1]["text"] = "he spent $100,000 in one year, and his team won $20,000 in prizes. " + PROSE
            page["regions"][2]["text"] = "The players were very happy with the results"
            page["regions"][2]["label"] = "Text"
            info["forms"] = {}
        self.assertEqual(self.call(change), [])

    def catalogue_fixture(self, mixed=False):
        pages, analysis = self.fixture()
        pages[1]["regions"][2]["text"] = PROSE + "He had carefully considered the whole matter"
        pages[2]["regions"] = [
            region("AUTOMOBILE", [100, 100, 1400, 150], "SectionHeader"),
            region("The AUTOMOBILE offers remarkable comfort for every journey and every driver.", [100, 200, 500, 500]),
            region("Your new AUTOMOBILE offers a choice of models for all your transport needs.", [600, 200, 1400, 500]),
            region("MODEL ONE FROM $ 1200\nMODEL TWO FROM $ 1500\nMODEL THREE FROM $ 2000", [600, 800, 1400, 1200]),
        ]
        if mixed:
            pages[2]["regions"].append(region(PROSE + "He had finished his journey.", [100, 1300, 500, 1800]))
        asm = rules.Assembler("fixture", pages, analysis)
        story = asm.new("story", title="The Long Road", page=1)
        asm.add(story, 1, 2)
        for i in range(len(pages[2]["regions"])):
            asm.add(story, 2, i)
        asm.open = story
        return asm, pages, analysis

    def catalogue_gap(self, mixed=False):
        asm, pages, analysis = self.catalogue_fixture(mixed)
        return asm.continued_prefix_columns(3, pages[3], analysis["pages"][3], list(range(8)), 8, None)

    def test_one_whole_product_catalogue_page_can_be_skipped_for_identity(self):
        self.assertEqual(self.catalogue_gap(), list(range(3, 8)))

    def test_mixed_narrative_and_catalogue_page_cannot_be_skipped(self):
        self.assertEqual(self.catalogue_gap(mixed=True), [])

    def test_catalogue_is_separated_only_after_complete_continuation_proof(self):
        asm, pages, analysis = self.catalogue_fixture()
        story = asm.open
        info = dict(analysis["pages"][3], cont_from=[(8, 999)])
        asm.story_page(3, pages[3], info)
        self.assertTrue({(1, 2), *((3, i) for i in range(3, 8))} <= set(story["keys"]))
        self.assertFalse(any(p == 2 for p, _ in story["keys"]))
        ad = next(r for r in asm.records if r["title"] == "AUTOMOBILE")
        self.assertEqual(ad["type"], "ad")
        self.assertEqual(set(ad["keys"]), {(2, i) for i in range(4)})
        self.assertTrue(all(asm.owner[rules.key(2, i)] == ad["article_id"] for i in range(4)))
        self.assertEqual(len({k for r in asm.records for k in r["keys"]}),
                         sum(len(r["keys"]) for r in asm.records))

    def test_rejected_continuation_does_not_authorize_catalogue_removal(self):
        asm, pages, analysis = self.catalogue_fixture()
        pages[3]["regions"][3]["text"] = "Startling. " + PROSE
        catalogue_pages = []
        kept = asm.continued_prefix_columns(3, pages[3], analysis["pages"][3],
                                            list(range(8)), 8, None, catalogue_pages)
        self.assertEqual(kept, [])
        self.assertEqual(catalogue_pages, [])
        self.assertEqual(len([k for k in asm.open["keys"] if k[0] == 2]), 4)

    def test_mixed_page_never_authorizes_catalogue_removal(self):
        asm, pages, analysis = self.catalogue_fixture(mixed=True)
        catalogue_pages = []
        self.assertEqual(asm.continued_prefix_columns(3, pages[3], analysis["pages"][3],
                         list(range(8)), 8, None, catalogue_pages), [])
        self.assertEqual(catalogue_pages, [])

    def test_catalogue_separation_preserves_other_owners_and_clears_stale_roles(self):
        asm, pages, analysis = self.catalogue_fixture()
        story = asm.open
        other = asm.new("ad", title="Printed offers", page=2)
        asm.steal(other, 2, 3)
        story["roles"][rules.key(2, 0)] = "title"
        asm.separate_continuation_catalogues([(2, "AUTOMOBILE")])
        self.assertNotIn(rules.key(2, 0), story["roles"])
        self.assertEqual(other["keys"], [(2, 3)])
        self.assertEqual(asm.owner[rules.key(2, 3)], other["article_id"])
        self.assertEqual(story["keys"], [(1, 2)])

    def test_short_dialogue_vetoes_whole_catalogue_proof(self):
        asm, pages, analysis = self.catalogue_fixture()
        pages[2]["regions"].append(region('"No," she whispered.', [100, 1300, 500, 1400]))
        catalogue_pages = []
        self.assertEqual(asm.continued_prefix_columns(3, pages[3], analysis["pages"][3],
                         list(range(8)), 8, None, catalogue_pages), [])
        self.assertEqual(catalogue_pages, [])


if __name__ == "__main__":
    unittest.main()
