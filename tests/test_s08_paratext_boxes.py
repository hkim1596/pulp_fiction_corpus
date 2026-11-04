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
