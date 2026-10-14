"""Real annotation handlers cannot cross an automated baseline replacement."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'pipeline'), str(ROOT / 'scripts'), str(ROOT / 'webapp')]
import article_guard
import upgrade_continuity as upgrade
import s13_publish as publish

spec = importlib.util.spec_from_file_location('guarded_webapp', ROOT / 'webapp/app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def document(aid='x_a001', backend='rules_v2.3'):
    return {'issue': 'x', 'backend': backend, 'articles': [
        {'article_id': aid, 'type': 'story', 'title': 'The Road', 'author': None,
         'text': 'He came home.', 'flags': [], 'roles': {}, 'pages': [1],
         'fragments': [{'page': 1, 'region_ids': [0]}]}],
        'furniture': [], 'unsorted': [],
        'checks': {'double_owned_regions': 0, 'unassigned_regions': 0}}


class ArticleGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / 'data/articles/x/articles.json'
        self.ann = self.root / 'data/annotations/x.jsonl'
        self.rule_rel = upgrade.output_paths('x')[0]
        self.put(self.live, document())
        self.put(self.root / self.rule_rel, document())
        self.put(self.live.parent / 'published.json', {'original': True})
        self.baseline = article_guard.assembly_sha256(self.root, 'x')
        self.patches = [patch.object(app, 'ROOT', str(self.root)),
                        patch.object(app, 'DATA', str(self.root / 'data')),
                        patch.object(app, 'ANNDIR', str(self.root / 'data/annotations')),
                        patch.object(app, 'ARCHDIR', str(self.root / 'data/archive'))]
        for p in self.patches:
            p.start()
        self.handler = app.H.__new__(app.H)
        self.handler.user = 'tester'
        self.handler._send = lambda code, body, *args: (code, body)
        self.handler._redirect = lambda location: (303, location)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def annotate(self, baseline=None):
        values = {'issue': 'x', 'article_id': 'x_a001', 'act': 'verify',
                  'assembly_sha256': self.baseline if baseline is None else baseline}
        return self.handler.do_annotate(lambda k, default='': values.get(k, default))

    def stage(self, root, stage, meta, mode, look_below):
        fresh = document('x_a002', upgrade.TARGET_BACKEND)
        # A changed ID with exactly preserved source regions is a valid rebuild.
        upgrade.validate_candidate(document(), fresh)
        for rel in (self.rule_rel, 'data/articles/x/articles.json'):
            self.put(stage / rel, fresh)
        self.put(stage / 'data/articles/x/published.json', {'new': True})
        if self.ann.exists():
            for rel in upgrade.output_paths('x')[-2:]:
                self.put(stage / rel, json.loads((root / rel).read_text()))
        return {}

    def test_fresh_form_logs_real_annotation_and_missing_token_does_not(self):
        self.assertEqual(self.annotate('')[0], 409)
        self.assertFalse(self.ann.exists())
        self.assertEqual(self.annotate()[0], 303)
        self.assertEqual(json.loads(self.ann.read_text())['article_id'], 'x_a001')

    def test_form_digest_matches_exact_rendered_machine_bytes(self):
        raw = self.live.read_bytes()
        self.assertEqual(app.effective_doc('x')['_assembly_sha256'], hashlib.sha256(raw).hexdigest())
        self.put(self.live, document('x_a001', upgrade.TARGET_BACKEND))
        self.assertEqual(self.annotate()[0], 409)  # ID exists but now denotes a changed baseline.
        self.assertFalse(self.ann.exists())

    def test_every_rendered_form_and_script_carries_the_rendered_baseline(self):
        doc = document()
        other = copy.deepcopy(doc['articles'][0])
        other.update(article_id='x_a002', fragments=[{'page': 1, 'region_ids': [1]}], pages=[1])
        doc['articles'].append(other)
        self.put(self.live, doc)
        baseline = article_guard.assembly_sha256(self.root, 'x')
        self.handler._page = lambda title, body, **kw: body
        with patch.object(app, 'issue_by_id', return_value={'id': 'x'}):
            rendered = self.handler.article_page('x_a001')
        class Forms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms, self.current = [], None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form':
                    self.current = {} if attrs.get('action') == '/annotate' else None
                    if self.current is not None:
                        self.forms.append(self.current)
                elif tag == 'input' and self.current is not None:
                    self.current[attrs.get('name')] = attrs.get('value')
            def handle_endtag(self, tag):
                if tag == 'form':
                    self.current = None
        parser = Forms()
        parser.feed(rendered)
        self.assertGreaterEqual(len(parser.forms), 6)
        self.assertTrue(all(f.get('assembly_sha256') == baseline for f in parser.forms))
        self.assertIn('BASELINE="' + baseline + '"', rendered)
        self.assertIn('params.assembly_sha256=BASELINE', rendered)

    def test_maintenance_rejects_before_reading_or_appending(self):
        self.put(self.root / 'data/corpus/CONTINUITY_UPGRADE', {'running': True})
        with patch.object(app, 'effective_doc', side_effect=AssertionError('must not read')):
            self.assertEqual(self.annotate()[0], 503)
        self.assertFalse(self.ann.exists())

    def test_inflight_first_annotation_finishes_before_upgrade_reads_baseline(self):
        entered, finish, started = threading.Event(), threading.Event(), threading.Event()
        original_effective = app.effective_doc
        def paused_effective(iid):
            entered.set()
            self.assertTrue(finish.wait(5))
            return original_effective(iid)
        def upgrade_job():
            started.set()
            return upgrade.upgrade_one(str(self.root), {'id': 'x'}, 'rules')
        before = self.live.read_bytes()
        with patch.object(app, 'effective_doc', side_effect=paused_effective), \
                patch.object(upgrade, 'stage_issue', side_effect=self.stage), ThreadPoolExecutor(2) as pool:
            human = pool.submit(self.annotate)
            self.assertTrue(entered.wait(5))
            machine = pool.submit(upgrade_job)
            self.assertTrue(started.wait(5))
            self.assertFalse(machine.done())
            finish.set()
            self.assertEqual(human.result(5)[0], 303)
            self.assertEqual(machine.result(5)['status'], 'complete')
        self.assertEqual(before, self.live.read_bytes())
        self.assertEqual(json.loads(self.ann.read_text())['article_id'], 'x_a001')

    def test_first_annotation_during_live_copy_waits_then_rejects_stale_form(self):
        copying, finish, posting = threading.Event(), threading.Event(), threading.Event()
        original_copy = upgrade.atomic_copy
        def paused_copy(source, target):
            if target == self.live:
                copying.set()
                self.assertTrue(finish.wait(5))
            return original_copy(source, target)
        def annotate_job():
            posting.set()
            return self.annotate()
        with patch.object(upgrade, 'stage_issue', side_effect=self.stage), \
                patch.object(upgrade, 'atomic_copy', side_effect=paused_copy), ThreadPoolExecutor(2) as pool:
            machine = pool.submit(upgrade.upgrade_one, str(self.root), {'id': 'x'}, 'rules')
            self.assertTrue(copying.wait(5))
            human = pool.submit(annotate_job)
            self.assertTrue(posting.wait(5))
            self.assertFalse(human.done())
            finish.set()
            self.assertEqual(machine.result(5)['status'], 'complete')
            self.assertEqual(human.result(5)[0], 409)
        self.assertFalse(self.ann.exists())
        self.assertEqual(json.loads(self.live.read_text())['articles'][0]['article_id'], 'x_a002')

    def test_uncoordinated_legacy_annotation_does_not_silently_skip_rollback(self):
        original_copy = upgrade.atomic_copy
        def unsafe_copy(source, target):
            if target == self.live:
                self.put(self.ann, {'action': 'verify', 'article_id': 'x_a001'})
            return original_copy(source, target)
        with patch.object(upgrade, 'stage_issue', side_effect=self.stage), \
                patch.object(upgrade, 'atomic_copy', side_effect=unsafe_copy):
            result = upgrade.upgrade_one(str(self.root), {'id': 'x'}, 'rules')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['recovery_required'])
        self.assertIn('automatic rollback is unsafe', result['error'])
        # No automatic retry may call this an intact human baseline.
        with patch.object(upgrade, 'stage_issue', side_effect=self.stage):
            retry = upgrade.upgrade_one(str(self.root), {'id': 'x'}, 'rules')
        self.assertTrue(retry['recovery_required'])

    def test_worker_death_receipt_keeps_maintenance_hold(self):
        receipt = self.root / 'data/corpus' / upgrade.BACKUP_NAME / 'x/receipt.json'
        self.assertFalse(upgrade.recovery_pending(self.root, [{'id': 'x'}]))
        self.put(receipt, {'status': 'committing'})
        self.assertTrue(upgrade.recovery_pending(self.root, [{'id': 'x'}]))
        self.put(receipt, {'status': 'failed'})
        self.assertFalse(upgrade.recovery_pending(self.root, [{'id': 'x'}]))

    def test_ordinary_publisher_shares_annotation_lock(self):
        locked, finish, publishing = threading.Event(), threading.Event(), threading.Event()
        before = self.live.read_bytes()
        def human_job():
            with article_guard.article_write_lock(self.root, 'x'):
                locked.set()
                self.assertTrue(finish.wait(5))
                app.ann_append('x', 'tester', 'verify', {'article_id': 'x_a001'})
        def publish_job():
            publishing.set()
            return publish.publish_issue('x', 'rules', force=True)
        with patch.object(publish, 'ROOT', str(self.root)), \
                patch.object(publish, 'LIVE', str(self.root / 'data/articles')), \
                patch.object(publish, 'ANN', str(self.root / 'data/annotations')), ThreadPoolExecutor(2) as pool:
            human = pool.submit(human_job)
            self.assertTrue(locked.wait(5))
            machine = pool.submit(publish_job)
            self.assertTrue(publishing.wait(5))
            self.assertFalse(machine.done())
            finish.set()
            human.result(5)
            self.assertEqual(machine.result(5)[0], 'held (corrected by a person)')
        self.assertEqual(self.live.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
