"""The explorer's issues table carries the item's kind (4 November 2026): the Overview counts the magazine issues as
the corpus and the books apart; the schema version is part of the database signature, so an older database rebuilds."""
import sys
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import explore_pages as EX


class KindColumn(unittest.TestCase):
    def test_schema_and_signature(self):
        self.assertIn("kind TEXT", EX.SCHEMA)
        self.assertGreaterEqual(EX.SCHEMA_VERSION, 2)
        EX._G.setdefault("ROOT", str(Path(__file__).resolve().parents[1]))
        a = EX._signature([])
        EX.SCHEMA_VERSION += 1
        try:
            self.assertNotEqual(a, EX._signature([]))
        finally:
            EX.SCHEMA_VERSION -= 1

    def test_overview_counts_magazines_only(self):
        src = (Path(__file__).resolve().parents[1] / "webapp" / "explore_pages.py").read_text(encoding="utf-8")
        self.assertIn("""where_iss = " AND ".join(["complete=1", "kind='magazine'"] + sc)""", src)
        self.assertIn("magazine issues (the corpus)", src)
        self.assertIn("books, kept as match partners", src)


if __name__ == "__main__":
    unittest.main()
