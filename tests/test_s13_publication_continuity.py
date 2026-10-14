"""Model confidence cannot by itself delete or split known story text."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s12_llm_link as link
import s13_publish as publish


class PublicationContinuity(unittest.TestCase):
    def fixture(self, text, decision, label="Text", bbox=None, later=None, separate=False, contents_title=None, force_change=False):
        regions = [{"text": "THE FIRST STORY", "label": "SectionHeader", "bbox": [0, 40, 900, 130]},
                   {"text": "The first story had already begun with a long body paragraph.", "label": "Text", "bbox": [0, 140, 900, 400]},
                   {"text": text, "label": label, "bbox": bbox or [0, 420, 900, 500]},
                   {"text": "The second paragraph is ordinary prose continuing the story.", "label": "Text", "bbox": [0, 520, 900, 700]}]
        if later:
            regions += [{"text": later, "label": "SectionHeader", "bbox": [0, 710, 900, 800]},
                        {"text": "The original story continues across the page after the interruption.", "label": "Text", "bbox": [0, 810, 900, 900]}]
        if force_change:
            regions[1] = {"text": "Send $1 now. COUPON Name____", "label": "Text", "bbox": [0, 140, 900, 400]}
        pages = {1: {"page": 1, "width": 1000, "height": 1000, "regions": regions}}
        rec = {"article_id": "t_a001", "type": "story", "title": "The First Story", "roles": {"1:0": "title"},
               "fragments": [{"page": 1, "region_ids": list(range(2 if separate else len(regions)))}],
               "pages": [1], "text": "original", "n_regions": len(regions), "flags": []}
        recs = [rec]
        if separate:
            recs.append({"article_id": "t_a002", "type": "story", "title": contents_title or text, "roles": {"1:2": "title"},
                         "fragments": [{"page": 1, "region_ids": [2, 3]}], "pages": [1], "text": "second", "n_regions": 2, "flags": []})
            if contents_title:
                recs[-1]["toc"] = {"title": contents_title, "page": 12}
                recs[-1]["roles"]["1:2"] = "note"
        rules = {"articles": recs, "furniture": []}
        owner = {f"1:{i}": (r, r["roles"].get(f"1:{i}")) for r in recs for i in r["fragments"][0]["region_ids"]}
        hints = link.rules_labels(pages, owner, set())
        boxes = {str(i + 1): {"joins": hints[f"1:{i}"]["joins"], "confidence": .99} for i in range(len(regions))}
        boxes["3"] = dict(decision, confidence=.999)
        if force_change:
            boxes["2"] = {"joins": "advert", "confidence": .999}
        answers = [{"page": 1, "keymap": {str(i + 1): i for i in range(len(regions))}, "boxes": boxes}]
        self.inputs = (pages, rules, answers)
        before = copy.deepcopy((pages, rules, answers))
        out = publish.publication_records("t", pages, rules, answers, {"out_ad", "out_furniture", "split", "split_later", "join"})
        self.assertEqual((pages, rules, answers), before)
        return out

    def test_narrative_is_not_ad_even_with_price_and_coupon_words(self):
        out = self.fixture("He read the coupon promising $1 and wondered whether the offer was a trap.", {"joins": "advert"})
        self.assertEqual(len(out["articles"]), 1)
        self.assertTrue(out["articles"][0]["llm"]["kept"])
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 1)

    def test_obvious_order_form_can_leave_story(self):
        out = self.fixture("Send $1 now for your FREE catalog.\nCOUPON\nName____ Address____", {"joins": "advert"})
        self.assertEqual({r["type"] for r in out["articles"]}, {"story", "ad"})
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 0)

    def test_narrative_at_page_edge_is_not_furniture(self):
        out = self.fixture("He watched the men walk silently over the hill and waited until the last of them disappeared.",
                           {"joins": "furniture"}, "PageHeader", [0, 0, 900, 90])
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 1)
        self.assertIn(2, out["articles"][0]["fragments"][0]["region_ids"])

    def test_actual_marginal_folio_can_leave_story(self):
        out = self.fixture("132", {"joins": "furniture"}, "PageFooter", [450, 960, 550, 990])
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 0)
        self.assertIn({"page": 1, "idx": 2}, out["furniture"])

    def test_inferred_paragraph_title_does_not_split_story(self):
        text = "A look of relief showed on the faces of the two foremen."
        out = self.fixture(text, {"joins": "new", "kind": "story", "title": text})
        self.assertEqual(len(out["articles"]), 1)
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 1)

    def test_true_printed_second_title_can_split(self):
        out = self.fixture("WOLVES OF THE NORTH", {"joins": "new", "kind": "story", "title": "Wolves of the North"}, "SectionHeader")
        self.assertEqual(len(out["articles"]), 2)
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 0)

    def test_intervening_title_cannot_take_later_original_resumption(self):
        out = self.fixture("WOLVES OF THE NORTH", {"joins": "new", "kind": "story", "title": "Wolves of the North"},
                           "SectionHeader", later="THE FIRST STORY")
        self.assertEqual(len(out["articles"]), 1)
        self.assertIn("visibly resumes", out["articles"][0]["continuity_holds"][0]["reason"])

    def test_distinct_printed_story_start_cannot_be_joined_away(self):
        out = self.fixture("WOLVES OF THE NORTH", {"joins": "previous"}, "SectionHeader", separate=True)
        self.assertEqual([r["article_id"] for r in out["articles"]], ["t_a001", "t_a002"])
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 1)

    def test_contents_placed_story_with_opening_credit_cannot_be_joined_away(self):
        out = self.fixture('Author of "Black Medicine" and other stories.', {"joins": "caption"}, "Caption",
                           separate=True, contents_title="Vale of the Corbies")
        self.assertEqual([r["article_id"] for r in out["articles"]], ["t_a001", "t_a002"])
        self.assertEqual(out["checks"]["model_changes_held_for_continuity"], 1)

    def test_upgrade_sentinel_holds_publisher_before_reading_or_writing_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "data" / "corpus" / "CONTINUITY_UPGRADE"
            sentinel.parent.mkdir(parents=True)
            sentinel.touch()
            with patch.object(publish, "ROOT", tmp), patch.object(publish, "settings", return_value={}), \
                    patch.object(publish, "all_states") as states, patch.object(publish, "log"), \
                    patch.object(publish, "publish_issue") as write:
                counts = publish.publish_all()
            self.assertEqual(counts["held_upgrade"], 1)
            self.assertEqual(counts["published"], 0)
            states.assert_not_called()
            write.assert_not_called()

    def test_held_proposal_remains_visible_after_publication_annotation(self):
        out = self.fixture("A long narrative sentence continues with people and places and action.", {"joins": "advert"})
        pages, rules, answers = self.inputs
        with patch.object(publish, "Labels", return_value=lambda key: key):
            count = publish.annotate_checked("t", out, rules, {}, {"out_ad": "apply"}, answers, {}, .9)
        rec = out["articles"][0]
        self.assertEqual(count, 1)
        self.assertTrue(rec["needs_look"])
        self.assertFalse(rec["model_check"]["agrees"])
        self.assertEqual(rec["model_check"]["continuity_holds"][0]["proposed"], "advert")
        self.assertTrue(any("kept story continuity" in flag for flag in rec["flags"]))

    def test_prose_cannot_be_hidden_as_caption_or_notice_after_another_change(self):
        text = "The officer waited by the doorway until all the guests had left the house."
        for joins in ("caption", "notice"):
            with self.subTest(joins=joins):
                out = self.fixture(text, {"joins": joins}, force_change=True)
                story = next(r for r in out["articles"] if r["type"] == "story")
                self.assertIn(text, story["text"])
                self.assertNotIn("1:2", story["roles"])

    def test_source_caption_and_abbreviated_notice_can_be_nonreading(self):
        for text, joins, label, role in (("An illustration of the old manor.", "caption", "Caption", "caption"),
                                         ("(Cont'd from p. 12)", "notice", "Text", "note")):
            with self.subTest(joins=joins):
                out = self.fixture(text, {"joins": joins}, label, force_change=True)
                story = next(r for r in out["articles"] if r["type"] == "story")
                self.assertNotIn(text, story["text"])
                self.assertEqual(story["roles"].get("1:2"), role)

    def test_narrative_mention_of_continuation_is_not_a_notice(self):
        text = "She continued from page 12 of the book until dawn."
        out = self.fixture(text, {"joins": "notice"}, force_change=True)
        story = next(r for r in out["articles"] if r["type"] == "story")
        self.assertIn(text, story["text"])


if __name__ == "__main__":
    unittest.main()
