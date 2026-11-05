"""scripts/box_recurrence_read.py (rules 2.14, step 2b, the reading): per story record, the recurring share, the
remainder, where the recurring boxes sit, their partners, and the counts the R-A guard needs."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import box_recurrence_read as BRR


class BoxRecurrenceRead(unittest.TestCase):
    def test_selftest(self):
        self.assertTrue(BRR.selftest())

    def test_position(self):
        self.assertEqual(BRR.position([False, False]), "none")
        self.assertEqual(BRR.position([True, True]), "whole")
        self.assertEqual(BRR.position([True, False, False]), "head")
        self.assertEqual(BRR.position([False, False, True, True]), "tail")
        self.assertEqual(BRR.position([True, False, True]), "head+tail")
        self.assertEqual(BRR.position([False, True, False]), "interior")
        self.assertEqual(BRR.position([True, False, True, False]), "interior")

    def test_bins(self):
        self.assertEqual(BRR._bin(0.3, BRR.SHARE_BINS), "0.25–0.5")
        self.assertEqual(BRR._bin(1.0, BRR.SHARE_BINS), "0.9–1")
        self.assertEqual(BRR._bin(2, BRR.REACH_BINS), "2")
        self.assertEqual(BRR._bin(7, BRR.REACH_BINS), "6–20")
        self.assertEqual(BRR._bin(500, BRR.SIZE_BINS), "151+")


if __name__ == "__main__":
    unittest.main()
