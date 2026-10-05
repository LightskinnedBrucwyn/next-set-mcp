import importlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from adapters.biolayne import sync_workflow as workflow

KEY = "fake-sync-key-for-tests-only"
TOKEN = "fake-bearer-token-for-tests-only"


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"NEXT_SET_SYNC_KEY": KEY}):
            cls.app = importlib.import_module("adapters.biolayne.app")
        cls.workout = (workflow.sync_v2.ROOT / 'tests/fixtures/workout.json').read_bytes()
        cls.catalog = (workflow.sync_v2.ROOT / 'tests/fixtures/catalog.json').read_bytes()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'training.db'
        self.raw = Path(temp.name) / 'raw'
        for module, name, value in (
            (workflow, 'DB_FILE', self.db), (workflow, 'RAW_DIR', self.raw),
            (workflow, 'SYNC_LOCK', threading.Lock()), (self.app, 'SYNC_KEY', KEY),
            (self.app, '_token', TOKEN), (self.app, '_expires', 1800003600),
        ):
            self.enterContext(patch.object(module, name, value))
        self.enterContext(patch.object(self.app.time, 'time', return_value=1800000000))
        self.network = self.enterContext(patch('requests.sessions.Session.request',
            side_effect=AssertionError('Live network forbidden')))
        self.get = self.enterContext(patch.object(self.app.requests, 'get'))
        self.get.side_effect = [self.response(self.workout), self.response(self.catalog)]
        self.client = self.enterContext(TestClient(self.app.app))
        self.headers = {'X-Next-Set-Key': KEY}

    def tearDown(self):
        self.network.assert_not_called()

    def response(self, raw, status=200):
        result = Mock(spec=requests.Response)
        result.status_code = status
        result.content = raw
        return result

    def post(self):
        return self.client.post('/sync/workout/100', headers=self.headers)

    def assert_clean(self, response):
        for secret in (KEY, TOKEN, 'Authorization', 'sensitive-upstream'):
            self.assertNotIn(secret, response.text)

    def test_success_and_summary(self):
        result = self.post()
        self.assertEqual(result.status_code, 200, result.text)
        self.assert_clean(result)
        body = result.json()
        self.assertEqual((body['workout_id'], body['master_workout_id']), (100, 13))
        self.assertEqual(body['database_totals'], {
            'import_snapshots': 1, 'exercise_catalog': 3, 'workout_instances': 1,
            'workout_sessions': 3, 'prescribed_exercises': 4, 'prescribed_sets': 8,
            'performed_exercises': 3, 'performed_sets': 6,
        })
        self.assertEqual((self.raw / body['snapshot']['workout_file']).read_bytes(), self.workout)
        self.assertEqual((self.raw / body['snapshot']['master_file']).read_bytes(), self.catalog)
        for file in self.raw.rglob('*.json'):
            for secret in (KEY, TOKEN, 'Authorization'):
                self.assertNotIn(secret.encode(), file.read_bytes())
        calls = self.get.call_args_list
        self.assertEqual(calls[1].kwargs['params'], {'masterWorkoutId': 13})
        self.assertFalse(calls[0].kwargs['allow_redirects'])
        summary = self.client.get('/training/summary', headers=self.headers)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()['database_totals']['performed_sets'], 6)
        self.assertEqual(summary.json()['latest_workout_date'], '2025-01-03T12:00:00')

    def test_repeat_idempotent_and_snapshots_never_overwritten(self):
        self.assertEqual(self.post().status_code, 200)
        originals = {p: p.read_bytes() for p in self.raw.rglob('*.json')}
        self.get.side_effect = [self.response(self.workout), self.response(self.catalog)]
        result = self.post().json()
        self.assertEqual(result['database_totals']['performed_sets'], 6)
        self.assertEqual(result['database_totals']['import_snapshots'], 2)
        self.assertEqual(len(list(self.raw.rglob('*.json'))), 4)
        self.assertTrue(all(p.read_bytes() == content for p, content in originals.items()))

    def test_protected_routes(self):
        for headers in ({}, {'X-Next-Set-Key': 'wrong'}):
            self.assertEqual(self.client.post('/sync/workout/100', headers=headers).status_code, 401)
            self.assertEqual(self.client.get('/training/summary', headers=headers).status_code, 401)
        self.get.assert_not_called()
        self.assertFalse(self.db.exists())

    def test_no_token(self):
        with patch.object(self.app, '_token', None):
            self.assertEqual(self.post().status_code, 401)
        self.get.assert_not_called()

    def test_expired_token(self):
        with patch.object(self.app, '_expires', 1800000000):
            self.assertEqual(self.post().status_code, 401)
        self.get.assert_not_called()

    def test_upstream_errors_no_import(self):
        for error, expected in [(requests.ConnectionError(TOKEN), 502),
                                (requests.Timeout(KEY), 504),
                                (requests.RequestException(TOKEN), 502)]:
            self.get.side_effect = error
            response = self.post()
            self.assertEqual(response.status_code, expected)
            self.assert_clean(response)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.raw.exists())

    def test_upstream_401_and_redirect(self):
        for status, expected in [(401, 401), (302, 502)]:
            response = self.response(TOKEN.encode(), status)
            self.get.side_effect = [response]
            result = self.post()
            self.assertEqual(result.status_code, expected)
            self.assert_clean(result)
            response.close.assert_called_once()
        self.assertFalse(self.db.exists())

    def test_malformed_workout(self):
        for raw in (b'not-json', b'{}', b'[]', b'null', b'\xff'):
            self.get.side_effect = [self.response(raw)]
            self.assertEqual(self.post().status_code, 502)
        self.assertFalse(self.db.exists())

    def test_conflicting_program_ids(self):
        rows = json.loads(self.workout)
        rows[1]['masterWorkoutId'] = 99
        self.get.side_effect = [self.response(json.dumps(rows).encode())]
        self.assertEqual(self.post().status_code, 502)
        self.assertEqual(self.get.call_count, 1)

    def test_conflicting_workout_id(self):
        self.assertEqual(self.client.post('/sync/workout/111', headers=self.headers).status_code, 502)

    def test_malformed_catalog_or_second_fetch_failure(self):
        for second in (self.response(b'[]'), self.response(b'not-json'), requests.ConnectionError(TOKEN)):
            self.get.side_effect = [self.response(self.workout), second]
            self.assertEqual(self.post().status_code, 502)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.raw.exists())

    def test_credential_bearing_source_rejected_before_snapshots(self):
        for field, value in [('note', TOKEN), ('cookie', 'private-cookie'), ('_wpnonce', 'private-nonce')]:
            rows = json.loads(self.workout)
            rows[0][field] = value
            self.get.side_effect = [self.response(json.dumps(rows).encode())]
            result = self.post()
            self.assertEqual(result.status_code, 502)
            self.assert_clean(result)
        self.assertFalse(self.raw.exists())

    def test_import_validation_failure_rolls_back_and_keeps_snapshots(self):
        self.assertEqual(self.post().status_code, 200)
        conn = sqlite3.connect(self.db)
        before = list(conn.iterdump())
        conn.close()
        rows = json.loads(self.workout)
        rows[-1]['sets'] = 2.5
        self.get.side_effect = [self.response(json.dumps(rows).encode()), self.response(self.catalog)]
        result = self.post()
        self.assertEqual(result.status_code, 422)
        self.assert_clean(result)
        conn = sqlite3.connect(self.db)
        self.assertEqual(list(conn.iterdump()), before)
        conn.close()
        self.assertEqual(len(list(self.raw.rglob('*.json'))), 4)

    def test_sqlite_failure_sanitized_and_lock_released(self):
        with patch.object(workflow.sync_v2, 'run_import', side_effect=sqlite3.OperationalError(TOKEN)):
            result = self.post()
        self.assertEqual(result.status_code, 500)
        self.assert_clean(result)
        self.assertFalse(workflow.SYNC_LOCK.locked())
        self.assertEqual(len(list(self.raw.rglob('*.json'))), 2)

    def test_concurrent_requests_rejected(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_get(url, **kwargs):
            if 'workout-exercises' in url:
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Test timed out')
                return self.response(self.workout)
            return self.response(self.catalog)
        self.get.side_effect = blocked_get
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.post)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.post().status_code, 409)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5).status_code, 200)
        self.assertEqual(len(list(self.raw.rglob('*.json'))), 2)

    def test_missing_summary_does_not_create_database(self):
        self.assertEqual(self.client.get('/training/summary', headers=self.headers).status_code, 404)
        self.assertFalse(self.db.exists())

    def test_snapshot_filename_collision_never_overwrites_or_imports(self):
        with patch.object(workflow, 'datetime') as clock, patch.object(workflow, 'uuid4') as uuid:
            clock.now.return_value.strftime.return_value = 'fixed-time'
            uuid.return_value.hex = 'fixed-id'
            self.assertEqual(self.post().status_code, 200)
            before = self.db.read_bytes()
            originals = {p: p.read_bytes() for p in self.raw.rglob('*.json')}
            self.get.side_effect = [self.response(self.workout), self.response(self.catalog)]
            result = self.post()
            self.assertEqual(result.status_code, 500)
            self.assertEqual(self.db.read_bytes(), before)
            self.assertTrue(all(p.read_bytes() == value for p, value in originals.items()))

    def test_snapshot_write_failure_does_not_import(self):
        with patch.object(Path, 'mkdir', side_effect=OSError(TOKEN)):
            result = self.post()
        self.assertEqual(result.status_code, 500)
        self.assert_clean(result)
        self.assertFalse(self.db.exists())
