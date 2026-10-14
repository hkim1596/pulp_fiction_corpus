import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("upgrade_continuity", ROOT / "scripts/upgrade_continuity.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)
sys.path.insert(0, str(ROOT / "pipeline"))
import s08_assemble_rules as rules


def doc(backend="rules_v2.3"):
    return {"issue": "x", "backend": backend, "articles": [
        {"article_id": "x_a001", "type": "story", "title": "The Road", "text": "He came home.",
         "flags": [], "roles": {}, "pages": [1], "fragments": [{"page": 1, "region_ids": [0]}]}],
        "furniture": [], "unsorted": [], "checks": {"double_owned_regions": 0, "unassigned_regions": 0}}


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "data/layout/x").mkdir(parents=True)
        self.rules_rel = upgrade.output_paths("x")[0]
        self.live_rel = "data/articles/x/articles.json"
        self.put(self.rules_rel, doc())
        self.put(self.live_rel, doc())
        self.put("data/articles/x/published.json", {"original": True})

    def tearDown(self):
        self.temp.cleanup()

    def put(self, rel, value):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value), encoding="utf-8")

    def fake_run(self, iid, meta, log):
        made = doc(upgrade.TARGET_BACKEND)
        upgrade.write_json_atomic(str(Path(rules.ROOT) / self.rules_rel), made)
        upgrade.write_json_atomic(str(Path(rules.ROOT) / "data/assembly_v2/rules/x/analysis.json"), {"new": True})
        return {"rules": made}

    def test_original_backup_is_never_replaced(self):
        target = self.root / "backups/x"
        original = upgrade.backup_once(self.root, "x", target)
        original_bytes = (target / "original" / self.rules_rel).read_bytes()
        self.put(self.rules_rel, doc(upgrade.TARGET_BACKEND))
        again = upgrade.backup_once(self.root, "x", target)
        self.assertEqual(original, again)
        self.assertEqual(original_bytes, (target / "original" / self.rules_rel).read_bytes())

    def test_invalid_candidate_never_reaches_live_or_rules(self):
        before = {p: upgrade.digest(self.root / p) for p in upgrade.output_paths("x")}
        def invalid(iid, meta, log):
            out = self.fake_run(iid, meta, log)
            bad = out["rules"]
            other = copy.deepcopy(bad["articles"][0])
            other["article_id"] = "x_a002"
            bad["articles"].append(other)
            upgrade.write_json_atomic(str(Path(rules.ROOT) / self.rules_rel), bad)
            return out
        with patch.object(rules, "run_issue", side_effect=invalid):
            result = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules")
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicate", result["error"])
        self.assertEqual(before, {p: upgrade.digest(self.root / p) for p in before})

    def test_partial_install_is_rolled_back(self):
        before = {p: upgrade.digest(self.root / p) for p in upgrade.output_paths("x")}
        original_copy = upgrade.atomic_copy
        count = 0
        def fail_after_one(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("injected install failure")
            return original_copy(source, target)
        with patch.object(rules, "run_issue", side_effect=self.fake_run), \
                patch.object(upgrade, "atomic_copy", side_effect=fail_after_one):
            result = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(before, {p: upgrade.digest(self.root / p) for p in before})
        # The committing receipt can safely recover and complete on a retry.
        with patch.object(rules, "run_issue", side_effect=self.fake_run):
            retry = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules")
        self.assertEqual(retry["status"], "complete")

    def test_bad_experimental_variant_keeps_entire_old_pair_while_main_upgrades(self):
        rom = "data/assembly_v2/rules_on_model/x/articles.json"
        analysis = "data/assembly_v2/rules_on_model/x/analysis.json"
        for failure in ("metadata", "coverage"):
            with self.subTest(failure=failure):
                self.put(self.rules_rel, doc())
                previous = doc()
                previous["articles"][0].update(work_id="protected-experimental-link", serial={"prev": {"issue": "prior"}})
                self.put(rom, previous)
                self.put(analysis, {"legacy": "must remain together with old articles"})
                before = {p: (self.root / p).read_bytes() for p in (rom, analysis)}
                def invalid_optional(iid, meta, log):
                    result = self.fake_run(iid, meta, log)
                    bad = doc(upgrade.TARGET_BACKEND)
                    if failure == "metadata":
                        bad["articles"][0].update(title="Unrelated", fragments=[{"page": 2, "region_ids": [0]}])
                    else:
                        extra = copy.deepcopy(bad["articles"][0]); extra["article_id"] = "duplicate-owner"
                        bad["articles"].append(extra)
                    upgrade.write_json_atomic(str(Path(rules.ROOT) / rom), bad)
                    upgrade.write_json_atomic(str(Path(rules.ROOT) / analysis), {"new": "must not escape alone"})
                    return result
                with patch.object(rules, "run_issue", side_effect=invalid_optional):
                    result = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules", force=True)
                self.assertEqual(result["status"], "complete", result)
                self.assertEqual(upgrade.version(upgrade.read_json(self.root / self.rules_rel)), upgrade.TARGET)
                self.assertEqual(before, {p: (self.root / p).read_bytes() for p in before})
                self.assertEqual(result["experimental_metadata_hold"]["variant"], "rules_on_model")
                self.assertNotIn(rom, result["candidate_sha256"])
                self.assertEqual(upgrade.read_json(self.root / rom)["articles"][0]["work_id"], "protected-experimental-link")
                self.assertTrue(any(a["kind"] == "held_experimental_output" for a in result["metadata_audit"]))

    def test_human_log_and_annotated_live_files_stay_byte_identical(self):
        ann = self.root / "data/annotations/x.jsonl"
        ann.parent.mkdir(parents=True)
        ann.write_text('{"op":"verified","article_id":"x_a001"}\n', encoding="utf-8")
        protected = [ann, self.root / self.live_rel, self.root / "data/articles/x/published.json"]
        before = [p.read_bytes() for p in protected]
        with patch.object(rules, "run_issue", side_effect=self.fake_run):
            result = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules")
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(before, [p.read_bytes() for p in protected])
        self.assertEqual(upgrade.version(upgrade.read_json(self.root / self.rules_rel)), upgrade.TARGET)
        self.assertTrue(result["publication"][0].startswith("held"))

    def test_added_unsorted_and_missing_regions_are_rejected(self):
        bad = doc(upgrade.TARGET_BACKEND)
        bad["unsorted"] = [{"key": "2:0"}]
        with self.assertRaisesRegex(ValueError, "adds unsorted"):
            upgrade.validate_candidate(doc(), bad)
        bad = doc(upgrade.TARGET_BACKEND)
        bad["articles"] = []
        with self.assertRaisesRegex(ValueError, "loses region"):
            upgrade.validate_candidate(doc(), bad)

    def test_links_survive_changed_record_id_and_are_deep_copied(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        old["articles"][0].update(work_id="work-1", serial={"prev": {"issue": "previous"}, "part_n": 2})
        new["articles"][0]["article_id"] = "x_a007"
        self.assertEqual(upgrade.preserve_links(old, new), 1)
        self.assertEqual(new["articles"][0]["work_id"], "work-1")
        self.assertEqual(new["articles"][0]["serial"], old["articles"][0]["serial"])
        new["articles"][0]["serial"]["prev"]["issue"] = "changed"
        self.assertEqual(old["articles"][0]["serial"]["prev"]["issue"], "previous")

    def test_plan_deduplicates_holds_collisions_and_requires_assembled(self):
        issues = [{"id": "x", "ia_identifier": "source"}, {"id": "x", "ia_identifier": "source"},
                  {"id": "collision", "ia_identifier": "one"}, {"id": "collision", "ia_identifier": "two"},
                  {"id": "new", "ia_identifier": "new"}]
        selected, note = upgrade.plan(self.root, issues, {"x": {"stages": {"assembled": {}}}})
        self.assertEqual([m["id"] for m in selected], ["x"])
        self.assertIn("collision", note["conflicting_ids_held"])
        self.assertEqual(note["skipped"]["not_assembled"], 1)

    def test_force_rechecks_current_issue_without_replacing_original_backup(self):
        with patch.object(rules, "run_issue", side_effect=self.fake_run):
            first = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules")
        self.assertEqual(first["status"], "complete")
        original = self.root / "data/corpus" / upgrade.BACKUP_NAME / "x/original" / self.rules_rel
        original_hash = upgrade.digest(original)
        def revised(iid, meta, log):
            out = self.fake_run(iid, meta, log)
            out["rules"]["articles"][0]["text"] = "His corrected ending."
            upgrade.write_json_atomic(str(Path(rules.ROOT) / self.rules_rel), out["rules"])
            return out
        with patch.object(rules, "run_issue", side_effect=revised):
            again = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules", force=True)
        self.assertEqual(again["status"], "complete")
        self.assertEqual(upgrade.read_json(self.root / self.rules_rel)["articles"][0]["text"], "His corrected ending.")
        self.assertEqual(upgrade.digest(original), original_hash)

    def test_force_plan_is_limited_to_one_explicit_current_issue(self):
        self.put(self.rules_rel, doc(upgrade.TARGET_BACKEND))
        issues, states = [{"id": "x", "ia_identifier": "source"}], {"x": {"stages": {"assembled": {}}}}
        self.assertEqual(upgrade.plan(self.root, issues, states)[0], [])
        with self.assertRaisesRegex(ValueError, "requires explicit"):
            upgrade.plan(self.root, issues, states, force=True)
        self.assertEqual([m["id"] for m in upgrade.plan(self.root, issues, states, only="x", force=True)[0]], ["x"])

    def test_explicit_batch_rejects_unknown_duplicate_empty_and_invalid_ids(self):
        issues, states = [{"id": "x", "ia_identifier": "source"}], {"x": {"stages": {"assembled": {}}}}
        self.put(self.rules_rel, doc(upgrade.TARGET_BACKEND))
        self.assertEqual([m["id"] for m in upgrade.plan(self.root, issues, states, only=["x"], force=True)[0]], ["x"])
        for selection in ([], ["x", "x"], ["unknown"], ["../x"], [12], {"issues": ["x"]}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                upgrade.plan(self.root, issues, states, only=selection, force=True)

    def test_unmappable_linked_record_is_never_retired(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        old["articles"][0].update(work_id="real-work", serial={"prev": {"issue": "older"}})
        new["articles"][0]["fragments"] = [{"page": 2, "region_ids": [0]}]
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            upgrade.preserve_links(old, new, audit=[])

    def test_corrected_small_heading_preserves_real_links_without_global_relaxation(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(author="Jane Doe", toc={"title": "The Road", "author": "Jane Doe", "page": 1},
                      work_id="real-work", serial={"prev": {"issue": "older"}},
                      fragments=[{"page": 1, "region_ids": [0, 1, 2, 3, 4]}])
        after.update(author="Jane Doe", toc=copy.deepcopy(before["toc"]), roles={"1:3": "title"},
                     fragments=[{"page": 1, "region_ids": [3, 4]}])
        predecessor = {"article_id": "x_before", "type": "story", "title": "Earlier Story",
                       "fragments": [{"page": 1, "region_ids": [0, 1, 2]}]}
        new["articles"].append(predecessor)
        pages = {1: {"regions": [{"text": "earlier story tail", "label": "Text"} for _ in range(3)]
                               + [{"text": "THE ROAD", "label": "SectionHeader"}, {"text": "new story", "label": "Text"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 1)
        self.assertEqual(after["work_id"], "real-work")
        self.assertEqual(after["serial"]["prev"], {"issue": "older"})
        self.assertEqual(audit[0]["kind"], "corrected_title_boundary")
        bad = copy.deepcopy(new)
        bad["articles"][0].pop("work_id")
        bad["articles"][0].pop("serial")
        bad["articles"][1]["type"] = "ad"
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            upgrade.preserve_links(old, bad, pages=pages)

    def test_proven_unlinked_serial_artifacts_are_audited_not_transferred(self):
        for rec in [
            {"title": "M", "work_title": "M", "title_as_printed": "Mending The Wounded With Glue",
             "serial": {"part_label": "ending", "source": "contents"},
             "toc": {"title": "Mending The Wounded With Glue", "type": "feature"}},
            {"title": "By A. L. Hodges", "author": "A. L. HODGES", "work_title": "By A. L. Hodges",
             "serial": {"part_label": "Part I", "source": "contents"},
             "toc": {"type": "serial_part", "blurb": 'Our Cover this month illustrates a scene from Part I of the story entitled, "The Universe Wreckers," by Edmond Hamilton, in which four travelers land.'}},
        ]:
            with self.subTest(title=rec["title"]):
                old, new = doc(), doc(upgrade.TARGET_BACKEND)
                old["articles"][0].update(copy.deepcopy(rec))
                new["articles"][0].update(copy.deepcopy(rec))
                audit = []
                self.assertEqual(upgrade.preserve_links(old, new, audit=audit), 0)
                self.assertNotIn("serial", new["articles"][0])
                self.assertEqual(audit[0]["kind"], "retired_unlinked_serial")
                linked = copy.deepcopy(old["articles"][0]); linked["work_id"] = "protected"
                self.assertIsNone(upgrade.retired_serial_reason(linked))

    def test_printed_poem_recovers_work_id_from_old_misplaced_contents_record(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(title="The Little Bird", author="Margaret Hays", work_id="poem-work",
                      work_title="The Little Bird", serial={"source": "contents", "part_n": None},
                      toc={"title": "The Little Bird", "author": "Margaret Hays", "page": 160, "type": "serial_part"},
                      fragments=[{"page": 1, "region_ids": [0, 1, 2]}])
        after.update(title="The Little Bird", author="Margaret Hays", type="poem", roles={"1:3": "title"},
                     toc=dict(before["toc"], type="poem"), fragments=[{"page": 1, "region_ids": [3, 4]}])
        new["articles"].append({"article_id": "preceding", "type": "story", "title": "Previous Story",
                                "fragments": [{"page": 1, "region_ids": [0, 1, 2]}]})
        pages = {1: {"regions": [{"text": "earlier story tail", "label": "Text"} for _ in range(3)]
                               + [{"text": "THE LITTLE BIRD", "label": "SectionHeader"},
                                  {"text": "A bird built a nest\nWithin the apple tree\nIts song came to me", "label": "Text"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 1)
        self.assertEqual(after["work_id"], "poem-work")
        self.assertNotIn("serial", after)
        self.assertTrue(any(a["kind"] == "retired_empty_serial_on_printed_poem" for a in audit))
        self.assertNotIn("work_id", new["articles"][1])

    def test_unlinked_contents_stub_recomputed_only_with_printed_title_and_byline(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(title="THE ROAD, by Jane Doe", author=None, work_title="THE ROAD, by Jane Doe",
                      serial={"source": "contents", "part_n": None},
                      toc={"title": "THE ROAD, by Jane Doe", "author": None, "page": 7})
        after.update(title="The Road", author="Jane Doe", work_title="The Road",
                     serial={"source": "contents", "part_n": 3},
                     toc={"title": "THE ROAD", "author": "Jane Doe", "page": 7},
                     fragments=[{"page": 4, "region_ids": [0, 1, 2]}],
                     roles={"4:0": "title", "4:1": "author"})
        pages = {4: {"regions": [{"text": "THE ROAD"}, {"text": "By Jane Doe"}, {"text": "Actual story body"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 0)
        self.assertEqual(after["serial"]["part_n"], 3)
        self.assertEqual(audit[0]["kind"], "recomputed_unlinked_contents_identity")
        before["work_id"] = "true-link"
        with self.assertRaisesRegex(ValueError, "cannot safely retain"):
            upgrade.preserve_links(old, new, pages=pages)

    def test_nonstory_merge_preserves_complete_excerpt_and_rejects_conflicts(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(type="ad", title="Old Offer", contains_excerpt=True,
                      excerpt_of={"issue": "other-issue", "title": "Quoted Story"})
        after.update(type="house", title="New Offer", fragments=[{"page": 1, "region_ids": [0, 1, 2]}])
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, audit=audit), 1)
        self.assertEqual(after["excerpt_of"], before["excerpt_of"])
        self.assertEqual(audit[0]["kind"], "nonstory_excerpt_containment")
        for changes in ({"type": "story"}, {"type": "feature"}, {"excerpt_of": {"issue": "conflict"}},
                        {"fragments": [{"page": 1, "region_ids": [0]}, {"page": 2, "region_ids": [1]}]}):
            bad = copy.deepcopy(new); bad["articles"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                upgrade.preserve_links(old, bad)

    def test_advertising_column_tail_can_be_removed_without_losing_work_link(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(work_id="real-work", toc={"title": "The Road", "page": 1}, roles={"1:0": "title"},
                      fragments=[{"page": 1, "region_ids": [0, 1]}, {"page": 2, "region_ids": [0, 1, 2]}])
        after.update(toc=copy.deepcopy(before["toc"]), roles={"1:0": "title"},
                     fragments=[{"page": 1, "region_ids": [0, 1]}])
        pages = {1: {"regions": [{"label": "SectionHeader", "text": "THE ROAD"}, {"text": "body"}]},
                 2: {"regions": [{"text": "Only $2 for this book", "bbox": [10, 10, 30, 20]},
                                  {"text": "Money Refunded", "bbox": [10, 20, 30, 30]},
                                  {"text": "MAIL COUPON TODAY", "bbox": [10, 30, 30, 40]},
                                  {"text": "Other story body", "bbox": [50, 10, 100, 40]}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 1)
        self.assertEqual(after["work_id"], "real-work")
        self.assertEqual(audit[0]["kind"], "removed_transactional_advertising_tail")
        pages[2]["regions"].append({"text": "Only $2 for this book, money refunded, mail coupon", "bbox": [10, 70, 30, 80]})
        pages[2]["regions"][0]["text"] = "Then the traveler reached home."
        with self.assertRaises(ValueError):
            upgrade.preserve_links(old, new, pages=pages)

    def test_author_only_empty_contents_serial_recomputed_from_printed_header(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(title="By Jane Doe", work_title="By Jane Doe", toc={"title": "By Jane Doe"},
                      serial={"source": "contents", "part_n": None},
                      fragments=[{"page": 1, "region_ids": [0, 1, 2, 3, 4]}])
        after.update(title="The Road", author="Jane Doe", title_source="page", author_source="page",
                     roles={"1:0": "title"}, fragments=[{"page": 1, "region_ids": [0, 1, 2, 3]}])
        pages = {1: {"regions": [{"label": "SectionHeader", "text": "The Road\nBy Jane Doe"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 0)
        self.assertNotIn("serial", after)
        self.assertEqual(audit[0]["kind"], "recomputed_unlinked_contents_identity")
        before["serial"]["part_n_source"] = {"issue": "trusted-source"}
        with self.assertRaises(ValueError):
            upgrade.preserve_links(old, new, pages=pages)

    def test_target_follows_current_rules_and_rechecks_completed_prior_release(self):
        self.assertEqual(upgrade.TARGET_BACKEND, f"rules_v{rules.VERSION}")
        self.assertEqual(upgrade.TARGET, (2, 6))
        self.put(self.rules_rel, doc("rules_v2.5"))
        self.put(f"data/corpus/{upgrade.BACKUP_NAME}/x/receipt.json", {"issue": "x", "status": "complete", "target": "rules_v2.5"})
        selected, _ = upgrade.plan(self.root, [{"id": "x", "ia_identifier": "source"}], {"x": {"stages": {"assembled": {}}}})
        self.assertEqual([x["id"] for x in selected], ["x"])
        with patch.object(rules, "run_issue", side_effect=self.fake_run):
            result = upgrade.upgrade_one(str(self.root), {"id": "x"}, "rules")
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["target"], "rules_v2.6")

    def test_cleaned_printed_ad_preserves_excerpt_without_giving_story_its_link(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(type="ad", title="LAW", contains_excerpt=True, excerpt_of={"issue": "source"},
                      fragments=[{"page": 1, "region_ids": [0, 1, 2, 3]}])
        after.update(type="ad", title="LAW", roles={"1:0": "title"},
                     fragments=[{"page": 1, "region_ids": [0, 1]}])
        story = {"article_id": "story", "type": "story", "title": "The Road", "roles": {"0:0": "title"},
                 "fragments": [{"page": 0, "region_ids": [0]}, {"page": 1, "region_ids": [2, 3]}]}
        new["articles"].append(story)
        pages = {1: {"regions": [{"text": "LAW", "label": "SectionHeader"},
                                  {"text": "Free books. Send to School Company, Dept. 5, Chicago."},
                                  {"text": "(Continued from page 1)\nHe waited beside the door."},
                                  {"text": "Then his friends arrived."}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 1)
        self.assertEqual(after["excerpt_of"], {"issue": "source"})
        self.assertNotIn("excerpt_of", story)
        self.assertEqual(audit[0]["kind"], "corrected_advertisement_boundary")
        story["type"] = "feature"
        with self.assertRaises(ValueError):
            upgrade.preserve_links(old, new, pages=pages)

    def test_empty_unlinked_contents_pseudorecord_is_recomputed_but_real_part_held(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before = old["articles"][0]
        before.update(title="Wrong Byline", work_title="Wrong Byline", serial={"source": "contents", "part_n": None})
        new["articles"][0]["fragments"] = [{"page": 2, "region_ids": [0]}]
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, audit=audit), 0)
        self.assertEqual(audit[0]["kind"], "recomputed_empty_unlinked_contents_metadata")
        before["serial"]["part_n"] = 2
        with self.assertRaises(ValueError):
            upgrade.preserve_links(old, new, audit=[])

    def test_unlinked_corrected_identity_keeps_meaningful_ordinal(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(title="Wrong Heading", work_title="Wrong Heading", serial={"source": "head", "part_n": 1},
                      fragments=[{"page": 1, "region_ids": [0, 1, 2]}])
        after.update(author="Jane Doe", work_title="The Road", serial={"source": "head", "part_n": 1},
                     roles={"1:0": "title", "1:1": "author"}, fragments=copy.deepcopy(before["fragments"]))
        pages = {1: {"regions": [{"label": "SectionHeader", "text": "THE ROAD"},
                                  {"text": "By Jane Doe"}, {"text": "body"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 0)
        self.assertEqual(after["work_title"], "The Road")
        self.assertEqual(after["serial"]["part_n"], 1)
        self.assertEqual(audit[0]["kind"], "recomputed_unlinked_printed_identity")
        after["serial"]["part_n"] = 2
        self.assertIsNone(upgrade.recomputed_unlinked_identity_reason(before, after, pages))
        after["serial"]["part_n"] = 1
        before["work_id"] = "protected"
        self.assertIsNone(upgrade.recomputed_unlinked_identity_reason(before, after, pages))

    def test_local_house_excerpt_inference_can_become_matching_complete_story(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(type="house", contains_excerpt=True, excerpt_of={"title": "The Road", "author": "Jane Doe"})
        after.update(author="Jane Doe", roles={"1:0": "title", "1:1": "author"},
                     fragments=[{"page": 1, "region_ids": [0, 1, 2]}])
        pages = {1: {"regions": [{"label": "SectionHeader", "text": "THE ROAD"},
                                  {"text": "By Jane Doe"}, {"text": "body"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 0)
        self.assertNotIn("excerpt_of", after)
        self.assertEqual(audit[0]["kind"], "recomputed_unlinked_printed_identity")
        before["excerpt_of"]["issue"] = "external"
        self.assertIsNone(upgrade.recomputed_unlinked_identity_reason(before, after, pages))

    def test_next_work_contents_blurb_does_not_assign_its_part_to_previous_piece(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(author="Jane Doe", work_title="The Road", serial={"source": "contents", "part_label": "Part II", "part_n": 2},
                      toc={"blurb": "The Chase A Three-Part Story. Part II Patrick Casey 126"},
                      fragments=[{"page": 1, "region_ids": [0, 1]}])
        after.update(copy.deepcopy(before)); after["roles"] = {"1:0": "title", "1:1": "author"}
        other = {"article_id": "other", "type": "story", "title": "The Chase A Three-Part Story", "author": "Patrick Casey",
                 "roles": {"2:0": "title"}, "fragments": [{"page": 2, "region_ids": [0]}]}
        new["articles"].append(other)
        pages = {1: {"regions": [{"label": "SectionHeader", "text": "THE ROAD"}, {"text": "By Jane Doe"}]},
                 2: {"regions": [{"label": "SectionHeader", "text": "The Chase A Three-Part Story\nBy Patrick Casey"}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 0)
        self.assertNotIn("serial", after)
        self.assertEqual(audit[0]["kind"], "retired_unlinked_serial")
        before["work_id"] = "external"
        self.assertIsNone(upgrade.retired_other_work_blurb_reason(before, new, pages))
        before.pop("work_id")
        pages[1]["regions"][1]["text"] = "By Jane Doe\nPart II"
        self.assertIsNone(upgrade.retired_other_work_blurb_reason(before, new, pages))

    def test_editorial_tail_after_serial_end_recomputes_only_unlinked_inline_identity(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(title="The Road, by Jane Doe", author=None, work_title="The Road, by Jane Doe",
                      toc={"title": "The Road, by Jane Doe", "page": 2}, serial={"source": "contents", "part_n": 1},
                      fragments=[{"page": 2, "region_ids": [0, 1, 2]}, {"page": 3, "region_ids": [0, 1]}])
        after.update(author="Jane Doe", toc={"title": "The Road", "author": "Jane Doe", "page": 2},
                     serial={"source": "contents", "part_n": 1}, roles={"2:0": "title", "2:1": "author"},
                     fragments=[{"page": 2, "region_ids": [0, 1, 2]}])
        new["articles"].append({"article_id": "editorial", "type": "feature", "title": "Editorial",
                                "fragments": [{"page": 1, "region_ids": [0]}, {"page": 3, "region_ids": [0, 1]}]})
        pages = {1: {"height": 1000, "regions": [{"text": "Editorial"},
                    {"label": "PageFooter", "text": "1", "bbox": [0, 950, 50, 990]}]},
                 2: {"regions": [{"label": "SectionHeader", "text": "THE ROAD"}, {"text": "By Jane Doe"},
                                  {"text": "Story body."}, {"text": "(To Be Continued)"}]},
                 3: {"regions": [{"text": "(Continued from Page 1.)"}, {"text": "The Editor."}]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 0)
        self.assertEqual(after["serial"]["part_n"], 1)
        self.assertEqual(audit[0]["kind"], "recomputed_unlinked_printed_identity")
        before["work_id"] = "true-external-link"
        self.assertIsNone(upgrade.recomputed_unlinked_identity_reason(before, after, pages, new))

        before.pop("work_id")
        pages[3]["regions"][0]["text"] = "(Continued from Page 99.)"
        self.assertIsNone(upgrade.recomputed_unlinked_identity_reason(before, after, pages, new))

    def test_linked_serial_relocation_requires_printed_identity_ordinal_and_page(self):
        old, new = doc(), doc(upgrade.TARGET_BACKEND)
        before, after = old["articles"][0], new["articles"][0]
        before.update(author="Jane Doe", work_id="real-work", work_title="The Road",
                      toc={"title": "The Road", "author": "Jane Doe", "page": 51},
                      serial={"part_n": 2, "prev": {"issue": "previous"}})
        after.update(author="Jane Doe", toc=copy.deepcopy(before["toc"]), serial={"part_n": 2},
                     roles={"3:0": "title", "3:1": "author"}, fragments=[{"page": 3, "region_ids": [0, 1, 2]}])
        previous = {"article_id": "previous-story", "type": "story", "title": "Other Story", "toc": {"page": 49},
                    "fragments": [{"page": 1, "region_ids": [0]}]}
        new["articles"].append(previous)
        def footer(n):
            return {"label": "PageFooter", "text": str(n), "bbox": [0, 950, 50, 990]}
        pages = {2: {"height": 1000, "regions": [footer(50)]},
                 3: {"height": 1000, "regions": [{"label": "SectionHeader", "text": "THE ROAD"},
                     {"text": "By Jane Doe"}, {"text": "New body"}, footer(3)]},
                 4: {"height": 1000, "regions": [footer(52)]}}
        audit = []
        self.assertEqual(upgrade.preserve_links(old, new, pages=pages, audit=audit), 1)
        self.assertEqual(after["work_id"], "real-work")
        self.assertEqual(after["serial"]["prev"], {"issue": "previous"})
        self.assertNotIn("work_id", previous)
        self.assertEqual(audit[0]["kind"], "relocated_named_serial_identity")
        pages[2]["regions"][0]["text"] = "99"
        self.assertFalse(upgrade.relocated_printed_serial(before, after, new, pages))



if __name__ == "__main__":
    unittest.main()
