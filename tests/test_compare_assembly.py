"""scripts/compare_assembly.py (rules 2.14 regression, 4 November 2026): the comparison reads the candidate on disk and
assembles in memory; the report lists what split out and which story texts changed."""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare_assembly as C


class CompareAssembly(unittest.TestCase):
    def test_summarise_and_report(self):
        old = {"articles": [{"type": "story", "title": "A", "author": "X", "text": "one two three four"},
                            {"type": "ad", "title": "buy", "text": "buy now"}]}
        by_type, stories, words = C.summarise(old)
        self.assertEqual(by_type, {"story": 1, "ad": 1})
        self.assertEqual(words, 4)
        rows = [{"issue": "i1", "rules_before": "rules_v2.13", "rules_after": "2.14", "types_before": by_type, "types_after": by_type,
                 "story_words_before": 4, "story_words_after": 3, "stories_before": 1, "stories_after": 1,
                 "split_out": [{"family": "masthead", "ad_class": "house_masthead", "title": "Entered as", "words": 20, "pages": [1]}],
                 "changed_stories": [{"title": "A", "author": "X", "change": "text", "words_before": 4, "words_after": 3, "left": "four", "arrived": "", "at_word": 3}],
                 "notes": 0, "rejected_bylines": []}, {"issue": "i2", "error": "no layout pages"}]
        md = C.report(rows)
        self.assertIn("| i1 | 1 → 1 | 4 → 3 | 1 | 1 | 0 |", md)
        self.assertIn("masthead", md)
        self.assertIn("no layout pages", md)
        self.assertIn("1 words left the story records", md)


if __name__ == "__main__":
    unittest.main()
