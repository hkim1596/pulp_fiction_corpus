"""Offline regression cases for preserving prose continuity through model cuts."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s12_llm_link as link
import corpus_lib
import s13_publish as publish


class ContinuityCuts(unittest.TestCase):
    def build(self, before, boundary, *, after="The rest of the story follows here.",
              title="A Different Piece", role=None, label="Text", gap=False, apply=None):
        def region(text, label="Text"):
            return {"text": text, "label": label, "bbox": [0, 0, 400, 100]}
        page2 = 3 if gap else 2
        pages = {1: {"page": 1, "regions": [region("THE RED MOON", "SectionHeader"), region(before)]},
                 page2: {"page": page2, "regions": [region(boundary, label), region(after)]}}
        roles = {"1:0": "title"}
        if role:
            roles[f"{page2}:0"] = role
        story = {"article_id": "t_a001", "type": "story", "title": "The Red Moon", "roles": roles,
                 "fragments": [{"page": 1, "region_ids": [0, 1]}, {"page": page2, "region_ids": [0, 1]}],
                 "pages": [1, page2], "text": "Original body with its continuation.", "n_regions": 4, "flags": []}
        records = [story]
        if gap:
            pages[2] = {"page": 2, "regions": [region("BUY WAR BONDS TODAY")]}
            records.append({"article_id": "t_a002", "type": "ad", "title": "War bonds", "roles": {},
                            "fragments": [{"page": 2, "region_ids": [0]}], "pages": [2],
                            "text": "BUY WAR BONDS TODAY", "n_regions": 1, "flags": []})
        rules_doc = {"articles": records, "furniture": []}
        untouched = copy.deepcopy(rules_doc)
        owner = {f"{f['page']}:{i}": (r, r["roles"].get(f"{f['page']}:{i}"))
                 for r in records for f in r["fragments"] for i in f["region_ids"]}
        hints = link.rules_labels(pages, owner, set())
        answers = []
        for pn, p in sorted(pages.items()):
            boxes = {str(i + 1): {"joins": hints[f"{pn}:{i}"]["joins"], "confidence": .99}
                     for i in range(len(p["regions"]))}
            if pn == page2:
                boxes["1"] = {"joins": "new", "kind": "story", "title": title, "confidence": .99}
            answers.append({"page": pn, "tier": "local", "keymap": {str(i + 1): i for i in range(len(p["regions"]))}, "boxes": boxes})
        with patch.object(link, "cut_rules", return_value={"same_title": True, "no_text": True,
                                                          "contents_max_words": 300, "continuity": True}):
            result = link.build_records("t", pages, answers, {"id": "t"}, rules_doc=rules_doc, rl=hints, apply=apply)
        self.assertEqual(rules_doc, untouched, "the rule assembly is never mutated")
        return result

    def assert_repaired(self, result, reason):
        story = next(r for r in result["articles"] if r["article_id"] == "t_a001")
        self.assertFalse(any(r["article_id"].startswith("t_a9") for r in result["articles"]))
        self.assertTrue(story["llm"]["kept"])
        self.assertEqual(story["text"], "Original body with its continuation.")
        self.assertIn(reason, story["llm"]["changes"][0])
        self.assertEqual(result["checks"]["cuts_set_aside"], 1)

    def test_lowercase_continues_unfinished_sentence(self):
        self.assert_repaired(self.build("He walked until he reached the", "edge of the dark forest and waited"),
                             "continuing sentence")

    def test_hyphenated_word_continues_across_advertisement(self):
        result = self.build("They entered the long forgotten under-", "ground chamber beneath the old house", gap=True)
        self.assert_repaired(result, "continuing sentence")
        self.assertEqual(next(r for r in result["articles"] if r["type"] == "ad")["text"], "BUY WAR BONDS TODAY")

    def test_known_chapter_stays_inside_story(self):
        self.assert_repaired(self.build("The earlier chapter ended here.", "THE EMPTY HOUSE", role="chapter",
                                        title="The Empty House", label="SectionHeader"), "chapter")

    def test_explicit_chapter_and_continuation_notice(self):
        for boundary, reason in (("CHAPTER III", "chapter"), ("(Continued from page 12)", "continuation notice")):
            with self.subTest(boundary=boundary):
                self.assert_repaired(self.build("The earlier text ended here.", boundary, label="SectionHeader"), reason)

    def test_real_title_remains_a_new_story(self):
        result = self.build("The first story ends here.", "WOLVES OF THE NORTH", title="Wolves of the North",
                            after="By Jane Smith. The snow fell all night.", label="SectionHeader")
        self.assertEqual(len(result["articles"]), 2)
        self.assertEqual(result["checks"]["cuts_set_aside"], 0)
        self.assertEqual(result["articles"][1]["title"], "Wolves of the North")

    def test_lowercase_alone_is_not_enough(self):
        result = self.build("The first story ends here.", "another world waited beyond the hills")
        self.assertEqual(len(result["articles"]), 2)
        self.assertEqual(result["checks"]["cuts_set_aside"], 0)

    def test_byline_is_not_sentence_continuation(self):
        result = self.build("He walked until he reached the", "by Jane Smith and John Jones")
        self.assertEqual(len(result["articles"]), 2)

    def test_numbered_furniture_is_not_chapter_evidence(self):
        result = self.build("He walked until he reached the", "III", label="PageHeader")
        self.assertEqual(result["checks"]["cuts_set_aside"], 0)

    def test_kind_filter_still_preserves_excluded_cuts(self):
        result = self.build("He walked until he reached the", "edge of the dark forest and waited", apply={"out_ad"})
        self.assertEqual(result["checks"]["cuts_set_aside"], 0)
        self.assertTrue(result["articles"][0]["llm"]["kept"])

    def test_short_unrelated_prose_is_not_title(self):
        pages = {1: {"regions": [{"text": "He paused."}, {"text": "THE RED MOON"}]}}
        self.assertFalse(link.is_title_box("The Last House on the Hill", "1:0", pages))
        self.assertTrue(link.is_title_box("The Red Moon", "1:1", pages))


class RebuiltReadingOrder(unittest.TestCase):
    def changed_record(self, pages, fragments, removed):
        rec = {"article_id": "t_a001", "type": "story", "title": "Test", "roles": {}, "flags": [],
               "fragments": fragments, "text": "Old text", "pages": sorted(pages), "n_regions": sum(len(f["region_ids"]) for f in fragments)}
        owner = {f"{f['page']}:{i}": (rec, None) for f in fragments for i in f["region_ids"]}
        hints = link.rules_labels(pages, owner, set())
        answers = [{"page": pn, "tier": "local", "keymap": {str(i + 1): i for i in range(len(p["regions"]))},
                    "boxes": {str(i + 1): {"joins": "furniture" if (pn, i) == removed else hints[f"{pn}:{i}"]["joins"],
                                            "confidence": .99} for i in range(len(p["regions"]))}}
                   for pn, p in sorted(pages.items())]
        with patch.object(link, "cut_rules", return_value={"same_title": True, "no_text": True, "contents_max_words": 300}):
            result = link.build_records("t", pages, answers, {"id": "t"}, rules_doc={"articles": [rec]}, rl=hints)
        return result["articles"][0]

    def test_rebuilt_text_reads_whole_left_column_before_right(self):
        boxes = [(0, 100, 450, 200), (550, 100, 1000, 200), (0, 300, 450, 400), (550, 300, 1000, 400), (0, 900, 1000, 950)]
        labels = ["LeftFirst", "RightFirst", "LeftLast", "RightLast", "Footer"]
        pages = {1: {"page": 1, "width": 1000, "height": 1000,
                     "regions": [{"text": word + " one two three four five six seven eight.", "label": "Text", "bbox": box}
                                 for word, box in zip(labels, boxes)]}}
        result = self.changed_record(pages, [{"page": 1, "region_ids": [0, 1, 2, 3, 4]}], (1, 4))
        self.assertEqual(result["fragments"], [{"page": 1, "region_ids": [0, 2, 1, 3]}])
        self.assertLess(result["text"].index("LeftLast"), result["text"].index("RightFirst"))
        self.assertNotIn("_page_order", result)

    def test_rebuild_keeps_corrected_printed_page_order(self):
        pages = {pn: {"page": pn, "regions": [{"text": f"Page{pn} body one two three four.", "label": "Text", "bbox": [0, 0, 400, 200]},
                                                {"text": f"Page{pn} footer", "label": "Text", "bbox": [0, 900, 400, 950]}]}
                 for pn in (1, 2)}
        result = self.changed_record(pages, [{"page": 2, "region_ids": [0, 1]}, {"page": 1, "region_ids": [0, 1]}], (1, 1))
        self.assertEqual([f["page"] for f in result["fragments"]], [2, 1])
        self.assertLess(result["text"].index("Page2"), result["text"].index("Page1"))


class AtomicDecisionLogs(unittest.TestCase):
    def test_failed_serialization_keeps_complete_previous_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pages.jsonl"
            old = '{"page": 7}\n'
            path.write_text(old)
            with self.assertRaises(TypeError):
                link.write_jsonl_atomic(str(path), [{"page": 1}, {"bad": object()}])
            self.assertEqual(path.read_text(), old)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_success_replaces_all_rows_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flags.jsonl"
            path.write_text('{"page": 7}\n')
            rows = [{"page": 1}, {"page": 2, "note": "café"}]
            link.write_jsonl_atomic(str(path), rows)
            self.assertEqual([json.loads(s) for s in path.read_text().splitlines()], rows)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


class SourceIdentityCallers(unittest.TestCase):
    def issues(self):
        return [{"id": "safe", "ia_identifier": "archive-safe"},
                {"id": "safe", "ia_identifier": "archive-safe"},
                {"id": "collision", "ia_identifier": "archive-a"},
                {"id": "collision", "ia_identifier": "archive-b"}]

    def test_publish_deduplicates_safe_and_keeps_collided_live_record(self):
        issues = self.issues()
        with patch.object(corpus_lib, "corpus_config", return_value={"issues": issues}), \
                patch.object(publish, "all_states", return_value={i["id"]: {"stages": {"assembled": {}}} for i in issues}), \
                patch.object(publish, "settings", return_value={}), patch.object(publish, "log") as logs, \
                patch.object(publish, "publish_issue", return_value=("current", "rules", 0, 0)) as write:
            counts = publish.publish_all()
        self.assertEqual([c.args[0] for c in write.call_args_list], ["safe"])
        self.assertEqual(counts["held_source_conflict"], 1)
        self.assertEqual(counts["held"], 1)
        self.assertEqual(counts["current"], 1)
        self.assertTrue(logs.called)

    def test_follow_never_schedules_conflicted_or_duplicate_id(self):
        issues = self.issues()
        with tempfile.TemporaryDirectory() as tmp:
            stop = Path(tmp) / "data" / "corpus" / "STOP_LLM"
            stop.parent.mkdir(parents=True)
            called = []
            def fake_link(iid, meta, *args):
                called.append((iid, meta["ia_identifier"]))
                stop.touch()
                return {"records": 1}
            with patch.object(link, "ROOT", tmp), patch.object(link, "load_pulp_env"), \
                    patch.object(corpus_lib, "corpus_config", return_value={"issues": issues}), \
                    patch.object(link, "all_states", return_value={i["id"]: {"stages": {"assembled": {}}} for i in issues}), \
                    patch.object(link, "lanes_answer", return_value=True), patch.object(link, "log"), \
                    patch.object(link, "event") as events, patch.object(link, "link_issue", side_effect=fake_link):
                link.follow(1)
            self.assertEqual(called, [("safe", "archive-safe")])
            hold = next(c for c in events.call_args_list if c.args[0] == "source_conflicts_held")
            self.assertEqual(hold.kwargs["count"], 1)

    def test_follow_holds_during_upgrade_and_resumes_when_marker_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "data" / "corpus"
            corpus.mkdir(parents=True)
            marker = corpus / "CONTINUITY_UPGRADE"
            marker.touch()
            stop = corpus / "STOP_LLM"
            def finish_wait(path, seconds):
                self.assertEqual(path, str(stop))
                self.assertEqual(seconds, 30)
                config.assert_not_called()
                link_issue.assert_not_called()
                marker.unlink()
            def fake_link(*args):
                stop.touch()
                return {"records": 1}
            with patch.object(link, "ROOT", tmp), patch.object(link, "load_pulp_env"), \
                    patch.object(corpus_lib, "corpus_config", return_value={"issues": self.issues()[:1]}) as config, \
                    patch.object(link, "all_states", return_value={"safe": {"stages": {"assembled": {}}}}), \
                    patch.object(link, "lanes_answer", return_value=True), patch.object(link, "log"), \
                    patch.object(link, "event"), patch.object(link, "_sleep_unless", side_effect=finish_wait) as wait, \
                    patch.object(link, "link_issue", side_effect=fake_link) as link_issue:
                link.follow(1)
            wait.assert_called_once()
            link_issue.assert_called_once()

if __name__ == "__main__":
    unittest.main()
