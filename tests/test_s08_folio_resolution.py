"""Resolve continuation destinations from surrounding printed page numbers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as rules


def page(scan, folio):
    return {"page": scan, "width": 1000, "height": 1400, "regions": [
        {"text": str(folio), "label": "PageFooter", "bbox": [450, 1330, 550, 1380], "order": 0}
    ]}


class FolioResolution(unittest.TestCase):
    def test_false_duplicate_cannot_steal_forward_destination(self):
        # OCR reads 653 as 658 five scans before the actual destination.
        pages = {n: page(n, n + 574) for n in range(75, 88)}
        pages[79] = page(79, 658)
        analysis = rules.analyse(pages)
        self.assertEqual(analysis["scan_of"](658), 84)
        self.assertEqual(analysis["folio_conflicts"][658], {"scans": [79, 84], "chosen": 84})

    def test_ambiguous_duplicate_uses_reliable_neighbour(self):
        pages = {10: page(10, 104), 11: page(11, 105), 15: page(15, 105)}
        analysis = rules.analyse(pages)
        self.assertIsNone(analysis["folio_conflicts"][105]["chosen"])
        self.assertNotIn(105, analysis["folio_index"])
        self.assertEqual(analysis["scan_of"](105), 11)

    def test_inserted_unnumbered_leaf_preserves_local_offset(self):
        pages = {1: page(1, 100), 2: page(2, 101), 3: page(3, 102),
                 5: page(5, 103), 6: page(6, 104), 8: page(8, 106)}
        analysis = rules.analyse(pages)
        self.assertEqual(analysis["scan_of"](103), 5)
        self.assertEqual(analysis["scan_of"](105), 7)

    def test_serial_suffix_cannot_start_inside_a_word(self):
        for title in ("Mending The Wounded With Glue", "Ascending Into Space", "Unending Flight"):
            self.assertEqual(rules.split_part(title), (title, None, None, None))

    def test_printed_serial_suffixes_still_parse(self):
        self.assertEqual(rules.split_part("The Road (Part Two of Three)"), ("The Road", "Part Two of Three", 2, 3))
        self.assertEqual(rules.split_part("The Road — Conclusion"), ("The Road", "Conclusion", None, None))


if __name__ == "__main__":
    unittest.main()
