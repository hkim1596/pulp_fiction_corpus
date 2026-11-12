"""Rules 2.15 (12 November 2026): an R-B box that the recurrence count does not confirm must pass a second test
(paratext_stands) — selling apparatus against narrative; a box that reads as text stays in its piece, flagged
"R-B withheld"; a box the count confirms is split as before."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import s08_assemble_rules as rules
from test_s08_paratext_boxes import BODY, BODY2, AD, box, page

LIVERY = ('"Certainly," replied the liveryman. "I\'ll tell you what I\'ll do. I\'ll charge you $75 if I can\'t get a passenger '
          'back, and I guarantee you a free ride home; but if I can, I\'ll ask only $50."')
NEWSROOM = '"What kind of a managing editor are you, McBride?" the editor of the New World News shouted into the phone.'
ENVELOPE = ("Jimmy bought a stamped envelope and a sheet of paper. With a lead pencil he addressed the envelope to himself and "
            "dropped it into the slot, and then he went back to the hotel and waited.")
REPLY = ("The best book for an amateur is American Food and Game Fishes, published by Doubleday, Doran & Co., Garden City, N. Y. "
         "It is sold at $4.00 and the price is fair. Address the publishers; I think they will mail it to you.")
DISPLAY_AD = ("GENUINE DIAMOND RINGS at FACTORY PRICES! Solid gold mountings, the LATEST styles. A bargain at $4.69! "
              "You will be proud of your ring. SOLID VALUE for your money!")


class SecondTest(unittest.TestCase):
    def test_selling_apparatus_stands(self):
        self.assertTrue(rules.paratext_stands("advertisement", AD))
        self.assertTrue(rules.paratext_stands("advertisement", DISPLAY_AD))            # no apparatus, but display type and "you"

    def test_narrative_does_not_stand(self):
        self.assertFalse(rules.paratext_stands("advertisement", LIVERY))               # dialogue that names a price
        self.assertFalse(rules.paratext_stands("advertisement", REPLY))                # a department's reply: prices, a publisher, no apparatus
        self.assertFalse(rules.paratext_stands("ownership statement", NEWSROOM))       # one cue ("managing editor are") is not a statement
        self.assertFalse(rules.paratext_stands("masthead", ENVELOPE))                  # "stamped envelope" in a story

    def test_real_masthead_and_ownership_stand(self):
        from test_s08_paratext_boxes import MASTHEAD, OWNERSHIP
        self.assertTrue(rules.paratext_stands("masthead", MASTHEAD))
        self.assertTrue(rules.paratext_stands("ownership statement", OWNERSHIP))
        self.assertTrue(rules.paratext_stands("masthead", "Popular Fiction Publishing Co., 840 North Michigan Avenue, Chicago."))

    def test_statement_and_masthead_set_in_several_boxes(self):
        """From the regression of 12 November (240 issues): the statement's parts and a story's copyright line stand
        on one cue; narration with the same words does not."""
        for t in ("Sworn to and subscribed before me this 16th day of September, 1942.",
                  "3. That the known bondholders, mortgagees, and other security holders owning or holding 1 per cent or more are: None.",
                  "Editor, managing editor, and business managers are: Publisher, Harold Hammond, 60 Hudson St., New York City."):
            self.assertTrue(rules.paratext_stands("ownership statement", t), t)
        for t in ('"The Book of the Tiger," copyright, 1926, by Harold Lamb.',
                  "Series began in the November, 1908, Railroad Man's Magazine. Single copies, 10 cents.",
                  "When sending for the button enclose a strong, self-addressed, unstamped envelope."):
            self.assertTrue(rules.paratext_stands("masthead", t), t)
        for t in ("To address a stamped envelope and slip it in with the outgoing mail was",
                  '"Yes. It has long been deemed sacrilege for mortal eyes to view the ancient writings. The single copy is kept in a great '
                  'vault, built of indestructible metals, and protectively sheathed to last for all time, and no man may open it without the word of the council."'):
            self.assertFalse(rules.paratext_stands("masthead", t), t)

    def test_recurrence_confirms(self):
        ad = {"n_issues": 12, "share": 1.0, "partners": [{"type": "ad"}, {"type": "story"}]}
        self.assertTrue(rules.recurrence_confirms(ad))
        self.assertTrue(rules.recurrence_confirms({"n_issues": 5, "partners": []}))
        self.assertFalse(rules.recurrence_confirms({"n_issues": 5, "partners": [{"type": "story"}, {"type": "story"}]}))   # a commonplace or a reprint
        self.assertFalse(rules.recurrence_confirms({"n_issues": 1, "partners": [{"type": "ad"}]}))
        self.assertFalse(rules.recurrence_confirms(None))


class InTheAssembly(unittest.TestCase):
    def pages(self):
        return {1: page(1, [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200),
                            box(BODY, 300, h=400), box(LIVERY, 720, h=150), box(AD, 890, h=120), box(BODY2, 1030, h=250)])}

    def assemble(self, evidence=None):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "box_recurrence"))
        if evidence is not None:
            json.dump(evidence, open(os.path.join(d, "box_recurrence", "x.json"), "w"))
        old = rules.RECURRENCE_DIR
        rules.RECURRENCE_DIR = os.path.join(d, "box_recurrence")
        try:
            pages = self.pages()
            analysis = rules.analyse(pages)
            return rules.assemble("x", pages, analysis), analysis
        finally:
            rules.RECURRENCE_DIR = old

    def test_the_cues_take_both_boxes(self):
        doc, analysis = self.assemble()
        self.assertEqual(analysis["pages"][1]["paratext"], {3: "advertisement", 4: "advertisement"})

    def test_unconfirmed_narrative_box_stays_in_the_story(self):
        doc, _ = self.assemble()
        story = [r for r in doc["articles"] if r["type"] == "story"][0]
        ads = [r for r in doc["articles"] if r.get("paratext")]
        self.assertIn("liveryman", story["text"])
        self.assertNotIn("Send no money", story["text"])
        self.assertEqual(len(ads), 1)
        self.assertIn("Send no money", ads[0]["text"])
        self.assertTrue(any(f.startswith("R-B withheld: 1 box(es) on p.1 read as advertisement") and "rules 2.15" in f for f in story["flags"]))
        self.assertIn("wagons could cross", story["text"])
        self.assertEqual(doc["backend"], "rules_v2.15")

    def test_confirmed_box_is_split_as_before(self):
        doc, _ = self.assemble({"1:3": {"n_tokens": 50, "share": 1.0, "n_issues": 9, "partners": [{"type": "ad"}]}})
        story = [r for r in doc["articles"] if r["type"] == "story"][0]
        self.assertNotIn("liveryman", story["text"])
        self.assertFalse(any("R-B withheld" in f for f in story["flags"]))

    def test_story_partners_do_not_confirm(self):
        doc, _ = self.assemble({"1:3": {"n_tokens": 50, "share": 1.0, "n_issues": 4, "partners": [{"type": "story", "author": "A", "work": "B"}]}})
        story = [r for r in doc["articles"] if r["type"] == "story"][0]
        self.assertIn("liveryman", story["text"])

    def test_switch_off_is_2_14(self):
        rules.RULES_215["on"] = False
        try:
            doc, _ = self.assemble()
        finally:
            rules.RULES_215["on"] = True
        story = [r for r in doc["articles"] if r["type"] == "story"][0]
        self.assertNotIn("liveryman", story["text"])
        self.assertEqual(len([r for r in doc["articles"] if r.get("paratext")]), 1)      # the two adjacent boxes joined one record


class Comparison(unittest.TestCase):
    def test_withheld_boxes(self):
        import compare_assembly as C
        old = {"articles": [{"article_id": "a1", "type": "story", "text": "s", "fragments": [{"page": 1, "region_ids": [2]}]},
                            {"article_id": "a2", "type": "ad", "paratext": "advertisement", "text": "one two three", "pages": [1],
                             "fragments": [{"page": 1, "region_ids": [3]}]},
                            {"article_id": "a3", "type": "ad", "paratext": "advertisement", "text": "kept", "fragments": [{"page": 1, "region_ids": [4]}]}]}
        new = {"articles": [{"article_id": "a1", "type": "story", "title": "The Black Stone", "text": "s one two three",
                             "fragments": [{"page": 1, "region_ids": [2, 3]}]},
                            {"article_id": "a2", "type": "ad", "paratext": "advertisement", "text": "kept", "fragments": [{"page": 1, "region_ids": [4]}]}]}
        w = C.withheld_boxes(old, new)
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0]["family"], w[0]["words"], w[0]["now_in"], w[0]["now_title"], w[0]["text"]), ("advertisement", 3, "story", "The Black Stone", "one two three"))


if __name__ == "__main__":
    unittest.main()
