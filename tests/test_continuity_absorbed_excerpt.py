"""The 2.7 upgrade's guard, case 'retired_absorbed_excerpt_inference' (p52, 18 October 2026).

Shapes taken from the refused records of 10d: a house block that 2.6 cut out of a
story as 'a house announcement quoting a story' (10_short_novels_magazine_1938_10,
record a032, 21 boxes on p. 130, now inside 'The Dressing-Room Champ'); an
advertisement whose text matched a story in another issue, split by 2.7 between an
advertisement and the story (fantastic_story_quarterly_undated, record a069).
"""
import copy
import sys
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import upgrade_continuity as u


def rec(article_id, type_, title, boxes, author=None, **extra):
    r = {"article_id": article_id, "type": type_, "title": title, "author": author, "flags": [], "roles": {},
         "fragments": [{"page": p, "region_ids": sorted(i for q, i in boxes if q == p)} for p in sorted({q for q, _ in boxes})]}
    r.update(extra)
    return r


def house_block():
    return rec("x_a032", "house", "Quite true. But McCafferty neglected to add", [(130, i) for i in range(4, 10)],
               ad_class="house_next_issue", contains_excerpt=True,
               flags=["house announcement quoting a story (contains_excerpt); left out of the reuse inventory",
                      "reads like an excerpt but no story in the corpus matches it (the quoted issue is not in the corpus)"])


def candidate_with_story(extra_articles=(), furniture=(), unsorted=()):
    story = rec("x_a030", "story", "The Dressing-Room Champ", [(126, 0), (126, 1)] + [(130, i) for i in range(0, 10)],
                author="Paul Adams", roles={"126:0": "title", "126:1": "author"},
                toc={"title": "The Dressing-Room Champ", "author": "Paul Adams", "page": 126})
    return {"articles": [story, *extra_articles],
            "furniture": [{"page": p, "idx": i} for p, i in furniture],
            "unsorted": [{"key": f"{p}:{i}"} for p, i in unsorted], "checks": {"double_owned_regions": 0}}


class AbsorbedExcerptInferenceTest(unittest.TestCase):
    def test_flag_only_house_block_inside_a_story_is_retired_into_the_audit(self):
        before, candidate, audit = house_block(), candidate_with_story(), []
        transfers = u.preserve_links({"articles": [before]}, candidate, pages={}, audit=audit)
        self.assertEqual(transfers, 0)
        self.assertEqual([a["kind"] for a in audit], ["retired_absorbed_excerpt_inference"])
        entry = audit[0]
        self.assertEqual(entry["original_metadata"], {"contains_excerpt": True})
        self.assertEqual(entry["proof"]["original_regions"], 6)
        self.assertEqual([(o["article_id"], o["shared_regions"], o["regions"]) for o in entry["proof"]["candidate_owners"]],
                         [("x_a030", 6, 12)])
        self.assertEqual(len(entry["evidence"]), 2)
        story = candidate["articles"][0]
        for k in ("contains_excerpt", "excerpt_of", "work_id", "serial"):
            self.assertNotIn(k, story)
        self.assertEqual(story["flags"], [])

    def text_matched_ad(self):
        return rec("x_a069", "ad", "You Can Learn to Be an ARTIST", [(160, i) for i in range(0, 8)], ad_class="trade",
                   contains_excerpt=True,
                   excerpt_of={"title": "Children of the Ray", "author": "J. Harvey Haggard",
                               "issue": "fantastic_story_quarterly_1950_04", "share": 0.42},
                   flags=["quotes 'Children of the Ray' (fantastic_story_quarterly_1950_04) verbatim: 42% of its 8-word runs occur there (matched by text)"])

    def test_text_matched_block_absorbed_into_a_reading_record_stays_refused(self):
        # The assembler may have merged an announcement quoting another issue's
        # story into reading text; that is for a person, not the guard.
        story = rec("x_a050", "story", "Children of the Ray", [(142, 0), (142, 1)] + [(160, i) for i in range(0, 5)],
                    author="J. Harvey Haggard", roles={"142:0": "title", "142:1": "author"})
        ad = rec("x_a051", "ad", "You Can Learn to Be an ARTIST", [(160, i) for i in range(5, 8)], ad_class="trade")
        candidate = {"articles": [story, ad], "furniture": [], "unsorted": [], "checks": {"double_owned_regions": 0}}
        with self.assertRaises(ValueError):
            u.preserve_links({"articles": [self.text_matched_ad()]}, candidate, pages={}, audit=[])
        for owner_type in ("feature", "poem", "letters"):
            story["type"] = owner_type
            with self.assertRaises(ValueError):
                u.preserve_links({"articles": [self.text_matched_ad()]}, copy.deepcopy(candidate), pages={}, audit=[])

    def test_text_matched_block_split_between_advertisements_or_contents_is_retired(self):
        # Still advertising matter: the cross-issue pass re-matches the new records.
        first = rec("x_a051", "ad", "You Can Learn to Be an ARTIST", [(160, i) for i in range(0, 5)], ad_class="trade")
        second = rec("x_a052", "ad", "FREE BOOK gives details!", [(160, i) for i in range(5, 8)], ad_class="trade")
        candidate = {"articles": [first, second], "furniture": [], "unsorted": [], "checks": {"double_owned_regions": 0}}
        audit = []
        self.assertEqual(u.preserve_links({"articles": [self.text_matched_ad()]}, candidate, pages={}, audit=audit), 0)
        self.assertEqual(audit[0]["kind"], "retired_absorbed_excerpt_inference")
        self.assertTrue(audit[0]["proof"]["text_matched"])
        self.assertEqual(sorted(o["type"] for o in audit[0]["proof"]["candidate_owners"]), ["ad", "ad"])
        self.assertEqual(audit[0]["original_metadata"]["excerpt_of"]["issue"], "fantastic_story_quarterly_1950_04")
        self.assertIn("matched by text", audit[0]["evidence"][0])
        for r in candidate["articles"]:
            self.assertNotIn("excerpt_of", r)
            self.assertNotIn("contains_excerpt", r)
            self.assertEqual(r["flags"], [])
        # a cover strip typed as a house record, now part of the contents record
        strip = rec("x_a005", "house", "THE BEST IN Science Fiction", [(3, i) for i in range(0, 27)], ad_class="house_self",
                    contains_excerpt=True, excerpt_of={"title": "Museum Piece", "author": "John Christopher 122",
                                                       "issue": "orbit_science_fiction_1953_12", "share": 0.31})
        toc = rec("x_a004", "toc", "Contents", [(3, i) for i in range(0, 29)])
        audit = []
        candidate = {"articles": [toc], "furniture": [], "unsorted": [], "checks": {"double_owned_regions": 0}}
        self.assertEqual(u.preserve_links({"articles": [strip]}, candidate, pages={}, audit=audit), 0)
        self.assertEqual(audit[0]["kind"], "retired_absorbed_excerpt_inference")
        self.assertNotIn("excerpt_of", toc)

    def test_a_record_with_work_identity_or_serial_is_still_refused(self):
        for field, value in (("work_id", "mag:the_road"), ("work_title", "The Road"),
                             ("serial", {"part_label": "Part II", "part_n": 2, "source": "contents"})):
            before = house_block()
            before[field] = value
            with self.assertRaises(ValueError):
                u.preserve_links({"articles": [before]}, candidate_with_story(), pages={}, audit=[])
        before = house_block()
        before["type"] = "story"
        with self.assertRaises(ValueError):
            u.preserve_links({"articles": [before]}, candidate_with_story(), pages={}, audit=[])

    def test_a_lost_or_unsorted_box_keeps_the_refusal(self):
        candidate = candidate_with_story()
        candidate["articles"][0]["fragments"][1]["region_ids"].remove(9)
        with self.assertRaises(ValueError):
            u.preserve_links({"articles": [house_block()]}, copy.deepcopy(candidate), pages={}, audit=[])
        unsorted = copy.deepcopy(candidate)
        unsorted["unsorted"] = [{"key": "130:9"}]
        with self.assertRaises(ValueError):
            u.preserve_links({"articles": [house_block()]}, unsorted, pages={}, audit=[])
        as_furniture = copy.deepcopy(candidate)
        as_furniture["furniture"] = [{"page": 130, "idx": 9}]
        audit = []
        u.preserve_links({"articles": [house_block()]}, as_furniture, pages={}, audit=audit)
        self.assertEqual(audit[0]["kind"], "retired_absorbed_excerpt_inference")
        self.assertEqual(audit[0]["proof"]["furniture_regions"], 1)

    def test_a_same_kind_record_holding_the_whole_block_is_left_to_the_general_paths(self):
        # A two-page house record still holds every old box: neither the one-page
        # containment transfer nor this retirement applies, so the issue stays refused.
        house = rec("x_a040", "house", "Coming next month", [(130, i) for i in range(2, 10)] + [(131, 0)], ad_class="house_next_issue")
        candidate = candidate_with_story()
        candidate["articles"][0]["fragments"][1]["region_ids"] = [0, 1]
        candidate["articles"].append(house)
        with self.assertRaises(ValueError):
            u.preserve_links({"articles": [house_block()]}, candidate, pages={}, audit=[])
        # the same block on one page is the existing containment transfer, unchanged
        house["fragments"] = [{"page": 130, "region_ids": list(range(2, 10))}]
        audit = []
        self.assertEqual(u.preserve_links({"articles": [house_block()]}, candidate, pages={}, audit=audit), 1)
        self.assertEqual(audit[0]["kind"], "nonstory_excerpt_containment")
        self.assertTrue(house["contains_excerpt"])

    def test_the_retirement_never_runs_before_a_unique_transfer(self):
        # An old house record kept with the same boxes transfers as before.
        before = house_block()
        same = rec("x_a032", "house", "Quite true. But McCafferty neglected to add", [(130, i) for i in range(4, 10)], ad_class="house_next_issue")
        candidate = {"articles": [same], "furniture": [], "unsorted": [], "checks": {"double_owned_regions": 0}}
        audit = []
        self.assertEqual(u.preserve_links({"articles": [before]}, candidate, pages={}, audit=audit), 1)
        self.assertEqual(audit, [])
        self.assertTrue(same["contains_excerpt"])


if __name__ == "__main__":
    unittest.main()
