"""The explorer's reuse source (29 October 2026): the corpus rehearsal's files when they are there, the pilot's otherwise;
and the decade table the overview draws from the rehearsal's summary when there is no pair table.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import explore_pages as EX


class ReuseSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.data = os.path.join(self.root, "data")
        os.makedirs(os.path.join(self.data, "reuse"))
        EX._G["DATA"] = self.data
        EX._G["ROOT"] = self.root
        EX._G["esc"] = lambda s: str(s) if s is not None else ""
        os.environ.pop("PULP_REUSE_SOURCE", None)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("PULP_REUSE_SOURCE", None)

    def _corpus(self):
        c = os.path.join(self.data, "reuse", "corpus_dryrun")
        os.makedirs(os.path.join(c, "tokens")); os.makedirs(os.path.join(c, "background"))
        json.dump({"generated": "2026-10-27 19:40"}, open(os.path.join(c, "tokens", "meta.json"), "w"))
        json.dump({"rows": 1}, open(os.path.join(c, "background", "summary_machine.json"), "w"))
        return c

    def test_pilot_when_no_corpus_files(self):
        self.assertEqual(os.path.basename(EX.reuse_dir()), "reuse")

    def test_corpus_when_its_files_exist(self):
        c = self._corpus()
        self.assertEqual(EX.reuse_dir(), c)
        self.assertIn("2026-10-27 19:40", EX.reuse_source_label())
        os.environ["PULP_REUSE_SOURCE"] = "pilot"
        self.assertEqual(os.path.basename(EX.reuse_dir()), "reuse")

    def test_sources_follow_the_source(self):
        c = self._corpus()
        EX._G["CONFIG"] = os.path.join(self.root, "config.json")
        EX._G["pages_of"] = lambda i: []
        paths = [p for p, _, _ in EX._sources()]
        self.assertTrue(any(p.endswith(os.path.join("corpus_dryrun", "background", "summary_machine.json")) for p in paths))

    def test_decade_rows_from_summary(self):
        summary = {"background": {
            "exact": {"k6": {"by_later_decade": {"1890": {"6": 0.0319, "10": 0.0002}, "1950": {"6": 0.0177, "10": 0.00008}}},
                      "k8": {"by_later_decade": {"1890": {"8": 0.0023}, "1950": {"8": 0.00085}}}},
            "time_table": {"exact_k6": {"1890s|0-2": {"n_pairs": 100, "p_any": 0.037}, "1890s|3-9": {"n_pairs": 300, "p_any": 0.03},
                                        "1950s|0-2": {"n_pairs": 1000, "p_any": 0.0186}},
                           "para_k10": {"1890s|0-2": {"n_pairs": 100, "p_any": 0.0001}, "1890s|3-9": {"n_pairs": 300, "p_any": 0.0},
                                        "1950s|0-2": {"n_pairs": 1000, "p_any": 0.00005}}}}}
        rows = EX.summary_decade_rows(summary)
        self.assertEqual([r["decade"] for r in rows], ["1890s", "1950s"])
        self.assertEqual(rows[0]["n"], 400)
        self.assertAlmostEqual(rows[0]["p6"], 3.19, places=2)
        self.assertAlmostEqual(rows[0]["p8"], 0.23, places=2)
        self.assertAlmostEqual(rows[0]["para"], 0.0025, places=4)
        self.assertEqual(EX.summary_decade_rows({}), [])


if __name__ == "__main__":
    unittest.main()
