"""The corpus-scale reuse overview (/reuse/corpus) renders every section of the rehearsal's summary (29 October 2026):
the background's strata read after the JSON round trip (string keys — the columns had shown 0.00 %), the share by decade of
the later story, authorship, the sampler check, the most unusual pairs and the two-part model's machinery check.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "webapp"), str(Path(__file__).resolve().parents[1] / "pipeline")]
import corpus_reuse_pages as CRP


class FakeRP:
    @staticmethod
    def _howto(t):
        return f"<details>{t}</details>"

    @staticmethod
    def _chart_row(a, b):
        return a + b

    @staticmethod
    def svg_bars(cats, series, title):
        return f"<svg data-title='{title}' data-n='{len(cats)}'></svg>"


class FakeEX:
    @staticmethod
    def N(v):
        return str(v)

    @staticmethod
    def _table(headers, rows):
        return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


def curve(p6, p7=None, p8=None, p10=None):
    d = {"6": p6, "7": p7 if p7 is not None else p6 / 4, "8": p8 if p8 is not None else p6 / 20, "10": p10 if p10 is not None else p6 / 200}
    return d


class CorpusReuseOverview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        snap = os.path.join(root, "data", "reuse", "corpus_dryrun")
        os.makedirs(os.path.join(snap, "tokens")); os.makedirs(os.path.join(snap, "background")); os.makedirs(os.path.join(snap, "para"))
        json.dump({"stories": 3, "min_tokens": 50, "issues": 2, "tokens": 1000, "vocabulary": 300, "generated": "2026-10-27 19:40", "files": 2},
                  open(os.path.join(snap, "tokens", "meta.json"), "w"))
        with open(os.path.join(snap, "tokens", "stories.jsonl"), "w") as f:
            for sid, iid in (("s1", "wt_1930_01"), ("s2", "ast_1931_02"), ("s3", "wt_1930_01")):
                f.write(json.dumps({"story_id": sid, "issue": iid, "meta": {"title": "T " + sid, "author": "A"}, "n_tokens": 300}) + "\n")
        json.dump({"matches": 10, "same_issue_matches": 1, "skipped_commonplace_shingles": 2, "pairs_with_reuse": 3, "stories_with_reuse": 3,
                   "matches_written": 4, "clusters": 2, "clusters_3plus_witnesses": 1, "longest_match": 40, "seconds_total": 5.0,
                   "length_hist": {"seed-9": 6, "10-19": 4}}, open(os.path.join(snap, "machine_k6_stats.json"), "w"))
        summary = {
            "rows": 100, "matched_rows": 50, "sampled_rows_draw1": 25, "sampled_rows_draw2": 25, "cross_issue_pairs_represented": 10000,
            "topic_tfidf_vs_embedding_corr": 0.75,
            "background": {
                "exact": {"k6": {"p_any": 0.02, "by_years_band": {"0-2": curve(0.03), "20+": curve(0.01), "3-9": curve(0.02)},
                                 "by_topic_q": {"1": curve(0.005), "4": curve(0.05)},
                                 "by_same_author": {"0": {"n": 90, **curve(0.02)}, "1": {"n": 10, **curve(0.18)}},
                                 "by_later_decade": {"1890": curve(0.032), "1950": curve(0.018)}},
                          "k8": {"p_any": 0.001, "by_years_band": {}, "by_topic_q": {}, "by_later_decade": {"1890": {"8": 0.0023}, "1950": {"8": 0.00085}}}},
                "paraphrase": {"k10": {"p_any": 0.00005, "by_years_band": {"0-2": {"20": 0.0001}}, "by_topic_q": {"4": {"20": 0.0002}}}},
                "time_table": {"exact_k6": {"1890s|0-2": {"n_pairs": 100, "p_any": 0.037, "mean_longest_given_any": 9.9}}}},
            "sampler_check": {"checks": {"exact_k6": {"draw1": {"p_any": 0.0196}, "draw2": {"p_any": 0.0197}, "abs_diff": {"p_any": 0.0001}}}},
            "unusual": {"exact_k6": {"n_matched_pairs": 3, "most_unusual": [
                {"a": "s1", "b": "s2", "longest": 500, "topic_q": 4, "years_band": "0-2", "stratum_pairs": 1200, "p_at_least": 0.0, "same_author": 0}]}},
            "models": {"note": "fit on an unweighted subsample", "exact_k6": {"n_pairs": 1000, "n_any": 500, "threshold": 6,
                       "part1_any": {"fixed": {"intercept": {"mean": -1.2, "sd": 0.1}, "later_year_z": {"mean": 2.4, "sd": 0.3}}, "story_effect_sd": 0.5, "seconds": 90.0}}},
        }
        json.dump(summary, open(os.path.join(snap, "background", "summary_machine.json"), "w"))
        CRP.bind({"ROOT": root, "DATA": os.path.join(root, "data"), "EX": FakeEX, "RP": FakeRP, "esc": lambda s: str(s) if s is not None else "",
                  "issue_by_id": lambda iid: {"magazine": iid.split("_")[0].upper(), "cover_date": "1930"}})
        CRP._CACHE.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_overview_renders_every_section(self):
        html = CRP.overview(render=None)
        self.assertIn("3.00%", html)                       # by years apart, read through the string keys
        self.assertIn("q4: 5.00%", html)                   # by topic quartile
        self.assertIn("same author: 18.00%", html)         # authorship
        self.assertIn("by decade of the later story", html)
        self.assertIn("1890s", html)
        self.assertIn("3.200", html)                       # 1890s six-word share
        self.assertIn("Sampler check", html)
        self.assertIn("Most unusual exact matches", html)
        self.assertIn("under 1 in 1,200", html)
        self.assertIn("machinery check", html)
        self.assertIn("later_year_z", html)
        self.assertIn("r = 0.75", html)
        self.assertNotIn("0.00%, 10-19: 0.00%", html)

    def test_helpers(self):
        self.assertEqual(CRP._p({"6": 0.5}, 6), 0.5)
        self.assertEqual(CRP._p({6: 0.5}, 6), 0.5)
        self.assertEqual(CRP._p({}, 6), 0.0)
        self.assertEqual([k for k, _ in CRP._bands({"20+": 1, "0-2": 2, "3-9": 3})], ["0-2", "3-9", "20+"])


if __name__ == "__main__":
    unittest.main()
