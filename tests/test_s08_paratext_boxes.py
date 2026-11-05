"""Rules 2.14 (4 November 2026): R-B paratext boxes inside a story — the statement of ownership, the masthead, a
publisher's address line, a prose advertisement — become their own house or ad records and the story resumes;
R-C a notice about the serial is a note, not story text; R-D editorial signatures and price strings are not authors,
house pseudonyms are."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules

BODY = ("He watched the road while she stood beside him. They had travelled far "
        "and he knew that his friends were waiting beyond the forest. ") * 12
BODY2 = ("the river ran high that spring and the men worked the ford in silence, waiting for the water "
         "to fall before the wagons could cross. ") * 10
OWNERSHIP = ("STATEMENT OF THE OWNERSHIP, MANAGEMENT, CIRCULATION, ETC., REQUIRED BY THE ACT OF CONGRESS OF AUGUST 24, "
             "1912. Of Weird Tales, published monthly at Indianapolis, Indiana, for October 1, 1936. State of Indiana, "
             "County of Marion, ss. Before me, a Notary Public in and for the State and county aforesaid, personally "
             "appeared William Sprenger, who, having been duly sworn according to law, deposes and says that he is the "
             "Business Manager. 1. That the names and addresses of the publisher, editor, managing editor, and business "
             "managers are: Publisher, Popular Fiction Publishing Co. 3. That the known bondholders, mortgagees, and other "
             "security holders owning or holding 1 per cent or more of total amount of bonds are: None.")
MASTHEAD = ("Entered as second-class matter August 27, 1931, at the Post Office at New York, N. Y., under the Act of "
            "March 3, 1879. Subscription rate $1.80 a year; single copies 15 cents. Names of all characters used in "
            "stories are fictitious.")
AD = ("Send no money! Write today for our FREE booklet and learn at home how thousands have found success. "
      "Dept. 5, 24 Union Square, New York. Money back guarantee if you are not satisfied. Address the coupon now.")


def box(text, y=100, label="Text", h=100):
    return {"text": text, "label": label, "bbox": [100, y, 900, y + h], "order": y}


def page(n, rows):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": rows + [box(str(n), 1330, "PageFooter", 30)]}


class ParatextBoxes(unittest.TestCase):
    def assemble(self, pages):
        analysis = rules.analyse(pages)
        return rules.assemble("x", pages, analysis), analysis

    def test_ownership_statement_inside_a_story_is_its_own_record(self):
        p1 = page(1, [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200),
                      box(BODY, 300, h=500), box(OWNERSHIP, 820, h=300), box(BODY2, 1140, h=150)])
        doc, analysis = self.assemble({1: p1})
        self.assertEqual(analysis["pages"][1]["paratext"], {3: "ownership statement"})
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        ads = [r for r in doc["articles"] if r["type"] in ("ad", "house")]
        self.assertEqual(len(stories), 1)
        self.assertNotIn("Notary Public", stories[0]["text"])
        self.assertIn("friends were waiting", stories[0]["text"])
        self.assertIn("wagons could cross", stories[0]["text"])          # the story resumes after the statement
        self.assertEqual(len(ads), 1)
        self.assertEqual(ads[0].get("ad_class"), "house_masthead")
        self.assertIn("Notary Public", ads[0]["text"])
        self.assertTrue(any("rules 2.14, R-B" in f for f in ads[0]["flags"]))

    def test_masthead_and_advertisement_boxes(self):
        p1 = page(1, [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200),
                      box(MASTHEAD, 300, h=80), box(BODY, 400, h=500), box(AD, 920, h=120), box(BODY2, 1060, h=200)])
        doc, analysis = self.assemble({1: p1})
        self.assertEqual(analysis["pages"][1]["paratext"], {2: "masthead", 4: "advertisement"})
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        self.assertNotIn("second-class matter", stories[0]["text"])
        self.assertNotIn("Send no money", stories[0]["text"])
        self.assertIn("wagons could cross", stories[0]["text"])
        kinds = sorted(r.get("ad_class") for r in doc["articles"] if r["type"] in ("ad", "house"))
        self.assertEqual(kinds, ["house_masthead", "trade"])

    def test_story_prose_that_mentions_an_address_is_not_paratext(self):
        prose = ("He gave her the address and walked to the mail train, thinking of nothing. She waited at the "
                 "station for an hour and then went home to her mother, who had a cold. " * 3)
        self.assertIsNone(rules.paratext_kind({"label": "Text"}, prose))
        self.assertIsNone(rules.paratext_kind({"label": "Text"}, "the notary public, he said, would never believe it; " * 5))

    def test_narrative_prose_about_money_is_not_an_advertisement(self):
        salary = ("Eddie worked for the magnificent salary of $800 for the season. But he had never asked for more, and his "
                  "coach said that was the way it had to be. He mailed the cheque to his mother at her address in the city.")
        letter = ("SIR: Don't you worry about old Eneas. He came here in October and said he was looking for his master. "
                  "He had the cup with him and would not sell it for $50 to any man. Yours, TOM JOHNSON.")
        self.assertIsNone(rules.paratext_kind({"label": "Text"}, salary))
        self.assertIsNone(rules.paratext_kind({"label": "Text"}, letter))
        ad = ("Hundreds of men and women between 18 and 50 make $10 to $20 in a single day giving Scientific Swedish Massage. "
              "Write for Anatomy Charts and booklet FREE. Address The College of Swedish Massage, Dept. 663, Chicago.")
        self.assertEqual(rules.paratext_kind({"label": "Text"}, ad), "advertisement")

    def test_serial_notice_is_a_note(self):
        p1 = page(1, [box("THE MOON POOL", 100, "SectionHeader"), box("By A. Merritt", 200),
                      box("This story began in the All-Story Weekly for June 22.", 300, h=40), box(BODY, 360, h=900)])
        doc, _ = self.assemble({1: p1})
        story = [r for r in doc["articles"] if r["type"] == "story"][0]
        self.assertNotIn("This story began", story["text"])
        self.assertIn("note", story["roles"].values())

    def test_byline_guard(self):
        for bad in ("By the Editor", "By THE EDITORS", "By THE $4.00 YEAR", "By Mail 30c", "By I", "By page", "By Editor and Readers"):
            self.assertIsNone(rules.byline_author(bad), bad)
        for good in ("By An Old Scout", "By A Trainer", "By Rufus King", "By A. E. van Vogt", "By Vargo Statten"):
            self.assertIsNotNone(rules.byline_author(good), good)

    def test_version(self):
        self.assertEqual(rules.VERSION, "2.14")


if __name__ == "__main__":
    unittest.main()


class RulesSwitch(unittest.TestCase):
    def test_switch_off_restores_2_13_behaviour(self):
        p1 = page(1, [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200),
                      box(BODY, 300, h=500), box(OWNERSHIP, 820, h=300), box(BODY2, 1140, h=150)])
        rules.RULES_214["on"] = False
        try:
            self.assertEqual(rules.analyse({1: p1})["pages"][1]["paratext"], {})
            self.assertIsNotNone(rules.byline_author("By the Editor"))
        finally:
            rules.RULES_214["on"] = True
        self.assertEqual(rules.analyse({1: p1})["pages"][1]["paratext"], {3: "ownership statement"})


NOTICE = ("WHEN you have read this issue of the magazine, let us know what you think of the stories it contains. "
          "Your letters guide the editors in choosing the stories that follow, and the Legal Reserve of good will "
          "between reader and editor is what keeps a magazine alive.")       # a house notice R-B does not see
VERSE = ('"And o\'er the hills, and far away, beyond their utmost purple rim, beyond the night, across the day, '
         'through all the world she followed him," he said, and the words were older than either of them.')


class RecurringBoxes(unittest.TestCase):
    """Rules 2.14 R-A (5 November 2026)."""

    def pages(self):
        p1 = page(1, [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200),
                      box(BODY, 300, h=400), box(NOTICE, 720, h=150), box(VERSE, 890, h=120), box(BODY2, 1030, h=250)])
        return {1: p1}

    def with_evidence(self, evidence):
        import tempfile, json, os
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "box_recurrence"))
        json.dump(evidence, open(os.path.join(d, "box_recurrence", "x.json"), "w"))
        old = rules.RECURRENCE_DIR
        rules.RECURRENCE_DIR = os.path.join(d, "box_recurrence")
        try:
            pages = self.pages()
            return rules.assemble("x", pages, rules.analyse(pages))
        finally:
            rules.RECURRENCE_DIR = old

    def test_verdicts(self):
        ad = {"n_tokens": 40, "share": 1.0, "n_issues": 12, "partners": [{"type": "ad", "author": None, "work": None}, {"type": "story", "author": "Fox B. Holden", "work": "A Gift for Terra"}]}
        self.assertEqual(rules.recurrence_verdict(ad, NOTICE, "The Black Stone", "Robert E. Howard"), "act")
        self.assertEqual(rules.recurrence_verdict(ad, NOTICE, "A Gift for Terra", "Fox B. Holden"), "same work")       # a reprint or a recap
        own = {"n_tokens": 90, "share": 0.9, "n_issues": 2, "partners": [{"type": "story", "author": "Dan Duane", "work": "The Hornet's Nest, Part II"}]}
        self.assertEqual(rules.recurrence_verdict(own, BODY, "In the Hornet's Nest", "Dan Duane"), "same author")
        quote = {"n_tokens": 25, "share": 0.61, "n_issues": 2, "partners": [{"type": "story", "author": "A. Lang", "work": "The Romance"}]}
        self.assertEqual(rules.recurrence_verdict(quote, VERSE, "Other", "Other"), "commonplace")
        self.assertEqual(rules.recurrence_verdict(quote, AD, "Other", "Other"), "act")                             # a selling cue: the ad every story absorbed
        self.assertIsNone(rules.recurrence_verdict({"n_tokens": 10, "share": 1.0, "n_issues": 9, "partners": []}, NOTICE, "", ""))
        self.assertIsNone(rules.recurrence_verdict({"n_tokens": 40, "share": 0.5, "n_issues": 9, "partners": []}, NOTICE, "", ""))

    def test_recurring_advertisement_is_split_out_and_the_verse_kept(self):
        ev = {"1:3": {"n_tokens": 40, "share": 1.0, "n_issues": 428, "partners": [{"type": "ad", "author": None, "work": None}, {"type": "story", "author": "Lee Owens", "work": "Life in the West"}]},
              "1:4": {"n_tokens": 25, "share": 0.61, "n_issues": 2, "partners": [{"type": "story", "author": "Andrew Lang", "work": "The Romance of the First Radical"}]}}
        doc = self.with_evidence(ev)
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        ads = [r for r in doc["articles"] if r["type"] in ("ad", "house")]
        self.assertEqual(len(stories), 1)
        self.assertNotIn("Legal Reserve", stories[0]["text"])
        self.assertIn("purple rim", stories[0]["text"])                       # the verse stays, flagged
        self.assertIn("wagons could cross", stories[0]["text"])
        self.assertTrue(any(f.startswith("commonplace?") for f in stories[0]["flags"]))
        self.assertTrue(any("split out as paratext (rules 2.14, R-A)" in f for f in stories[0]["flags"]))
        self.assertEqual(len(ads), 1)
        self.assertEqual(ads[0]["paratext"], "recurring box")
        self.assertEqual(ads[0]["split_from"], stories[0]["article_id"])
        self.assertEqual(ads[0]["recurrence"]["n_issues"], 428)
        self.assertIn("Legal Reserve", ads[0]["text"])

    def test_mostly_recurring_record_is_left_whole(self):
        ev = {"1:2": {"n_tokens": 300, "share": 1.0, "n_issues": 3, "partners": [{"type": "ad", "author": None, "work": None}]},
              "1:3": {"n_tokens": 40, "share": 1.0, "n_issues": 3, "partners": [{"type": "ad", "author": None, "work": None}]}}
        doc = self.with_evidence(ev)
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        self.assertIn("Legal Reserve", stories[0]["text"])
        self.assertTrue(any(f.startswith("mostly recurring") for f in stories[0]["flags"]))

    def test_same_work_is_kept(self):
        ev = {"1:3": {"n_tokens": 40, "share": 1.0, "n_issues": 3, "partners": [{"type": "story", "author": "Robert E. Howard", "work": "The Black Stone"}]}}
        doc = self.with_evidence(ev)
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertIn("Legal Reserve", stories[0]["text"])
        self.assertTrue(any("same work or the same author" in f for f in stories[0]["flags"]))

    def test_no_evidence_no_change(self):
        doc = self.with_evidence({})
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertIn("Legal Reserve", stories[0]["text"])
        self.assertFalse(any("R-A" in f for f in stories[0]["flags"]))
