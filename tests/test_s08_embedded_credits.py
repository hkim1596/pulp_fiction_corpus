"""Embedded bylines can be source credits or signed advertising endorsements."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


BODY = ("He watched the road while she stood beside him. They had travelled far "
        "and he knew that his friends were waiting beyond the forest. ") * 12
CREDIT = "From a Fragmentary and Unpublished Manuscript\nBy ALEXANDRE DUMAS"


def box(text, y=100, label="Text", h=100):
    return {"text": text, "label": label, "bbox": [100, y, 900, y + h], "order": y}


def page(n, rows):
    return {"page": n, "width": 1000, "height": 1400,
            "regions": rows + [box(str(n), 1330, "PageFooter", 30)]}


class EmbeddedCredits(unittest.TestCase):
    def source_pages(self):
        return {1: page(1, [box("D'ARTAGNAN", 100, "SectionHeader"),
                            box("By ALEXANDRE DUMAS", 220),
                            box(BODY + "It seems that he had been born in the", 400, h=800)]),
                2: page(2, [box(CREDIT, 100, "SectionHeader"),
                            box("Translated and Augmented by\nH. BEDFORD-JONES", 220),
                            box("abbey while the queen was there. " + BODY, 400, h=800)])}

    def test_manuscript_credit_cannot_break_adjacent_narrative_sentence(self):
        pages = self.source_pages()
        analysis = rules.analyse(pages)
        self.assertEqual(analysis["pages"][2]["starts"], [])
        self.assertEqual(analysis["pages"][2]["source_credits"], [0, 1])
        doc = rules.assemble("x", pages, analysis)
        stories = [r for r in doc["articles"] if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["pages"], [1, 2])
        self.assertEqual(stories[0]["roles"]["2:0"], "note")
        self.assertEqual(stories[0]["roles"]["2:1"], "note")
        self.assertIn("born in the abbey", stories[0]["text"])
        self.assertEqual(doc["checks"]["unassigned_regions"], 0)
        self.assertEqual(doc["checks"]["double_owned_regions"], 0)

    def test_genuine_combined_story_title_still_opens_after_unfinished_prose(self):
        pages = self.source_pages()
        pages[2]["regions"][0]["text"] = "The Lost Manuscript\nBy ALEXANDRE DUMAS"
        self.assertEqual(rules.analyse(pages)["pages"][2]["starts"][0]["title"], "The Lost Manuscript")

    def test_manuscript_title_without_adjacent_sentence_evidence_still_starts(self):
        for mode in ("no_previous_page", "complete_previous_sentence", "new_capitalized_paragraph", "explicit_end"):
            with self.subTest(mode=mode):
                pages = self.source_pages()
                if mode == "no_previous_page":
                    pages.pop(1)
                elif mode == "complete_previous_sentence":
                    pages[1]["regions"][2]["text"] += "."
                elif mode == "new_capitalized_paragraph":
                    pages[2]["regions"][2]["text"] = BODY
                else:
                    pages[1]["regions"].insert(-1, box("THE END", 1250))
                self.assertEqual(rules.analyse(pages)["pages"][2]["starts"][0]["title"],
                                 "From a Fragmentary and Unpublished Manuscript")

    def endorsement_page(self):
        return page(1, [
            box("WHAT I THINK OF\nBy Judge\nPELMANISM - Ben B. Lindsey", 100, "SectionHeader"),
            box(("PELMANISM is a useful course. I enrolled in Pelmanism and I recommend Pelmanism. " * 8), 250, h=300),
            box("(Signed) Ben B. Lindsey.", 600),
            box("Pelmanism offers training. Your free booklet is waiting. Mail the coupon now.", 750),
            box("The Pelman Institute of America", 900, "SectionHeader"),
            box("Name and Address. Please send me your free booklet.", 1100)])

    def test_signed_product_offer_does_not_open_a_story(self):
        analysis = rules.analyse({1: self.endorsement_page()})
        self.assertEqual(analysis["pages"][1]["starts"], [])
        self.assertIn("signed product endorsement", analysis["pages"][1]["rejected_starts"][0]["rejected"])

    def test_unrelated_coupon_cannot_reclassify_signed_editorial(self):
        p = self.endorsement_page()
        p["regions"][3]["text"] = "Our new car offers comfort. Your free booklet is waiting. Mail the coupon now."
        p["regions"][4]["text"] = "The Motor Company"
        self.assertTrue(rules.analyse({1: p})["pages"][1]["starts"])

    def test_offer_without_matching_signature_cannot_reject_byline(self):
        p = self.endorsement_page()
        p["regions"][2]["text"] = "(Signed) Different Person."
        self.assertTrue(rules.analyse({1: p})["pages"][1]["starts"])

    def test_contents_story_is_not_rejected_by_product_endorsement_guard(self):
        p = self.endorsement_page()
        start = {"byline_idx": 0, "embedded_title": True, "author": "Judge PELMANISM - Ben B. Lindsey",
                 "toc": {"title": "What I Think of Pelmanism", "type": "feature"}}
        self.assertFalse(rules.signed_product_endorsement(p, start))


if __name__ == "__main__":
    unittest.main()
