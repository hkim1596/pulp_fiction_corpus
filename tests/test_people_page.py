"""The people page (site v0.29.0, 4 November 2026): the three co-principal investigators in the order of
config/people.json (Tenen, Kim, Kang), each with their lines, role and links; a missing file renders a note, not an
error; the page is in the Explore row and is served before the passcode.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp")]
import people_pages as PP

ROOT = Path(__file__).resolve().parents[1]


def _esc(s):
    import html
    return html.escape(str(s), quote=True)


class PeoplePage(unittest.TestCase):
    def setUp(self):
        PP.bind({"ROOT": str(ROOT), "esc": _esc})

    def test_repository_file_renders_in_order(self):
        html = PP.people_html()
        names = ["Dennis Yi Tenen", "Heejin Kim", "Sujin Kang"]
        pos = [html.index(n) for n in names]
        self.assertEqual(pos, sorted(pos), "the order of config/people.json is kept")
        self.assertIn("Co-principal investigators", html)
        self.assertIn("Narrative Intelligence Lab", html)
        self.assertIn("https://dennistenen.com/about/", html)
        self.assertIn("https://www.digihumeng.org", html)
        self.assertIn("Kyungpook Institute of Humanities and Social Sciences", html)
        self.assertIn("<a href=", html)

    def test_missing_file_is_a_note(self):
        with tempfile.TemporaryDirectory() as d:
            html = PP.people_html(root=d)
            self.assertIn("missing or empty", html)

    def test_escaping_and_optional_fields(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "config"))
            json.dump({"groups": [{"heading": "A & B", "note": "n", "people": [{"name": "X <y>", "lines": ["l"]}]}]},
                      open(os.path.join(d, "config", "people.json"), "w"))
            html = PP.people_html(root=d)
            self.assertIn("A &amp; B", html)
            self.assertIn("X &lt;y&gt;", html)
            self.assertNotIn("class='fine'", html.split("X &lt;y&gt;")[1])   # no role, no links → no fine lines

    def test_nav_and_public_route(self):
        src = (ROOT / "webapp" / "app.py").read_text(encoding="utf-8")
        self.assertIn('("/people", "People")', src)
        before = src.index('if path == "/people"')
        self.assertLess(before, src.index('if not self._member():\n            return self._redirect("/login")'))


if __name__ == "__main__":
    unittest.main()
