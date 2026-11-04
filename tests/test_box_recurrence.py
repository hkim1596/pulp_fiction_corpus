"""scripts/box_recurrence.py (rules 2.14, step 2b): a box whose 8-word shingles occur in boxes of three or more issues
is flagged with its share, its reach and its partners; a story's own text in two issues is not."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import box_recurrence as BR


class BoxRecurrence(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(BR.selftest())

    def test_shingles(self):
        n, hs = BR.box_shingles("one two three four five six seven eight nine")
        self.assertEqual((n, len(hs)), (9, 2))
        self.assertEqual(BR.box_shingles("one two")[1], [])
        self.assertEqual(BR.box_shingles("One TWO three four five six seven eight")[1], BR.box_shingles("one two three four five six seven eight")[1])


if __name__ == "__main__":
    unittest.main()
