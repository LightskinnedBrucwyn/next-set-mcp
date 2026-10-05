import importlib
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import coach_api
from adapters.biolayne import sync_v2
from adapters.biolayne import sync_workflow

SYNC = 'fake-sync-key-for-tests-only'
COACH = 'fake-distinct-read-key-for-tests-only'


class CoachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'NEXT_SET_SYNC_KEY': SYNC}):
            cls.app = importlib.import_module('adapters.biolayne.app')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / 'coach.db'
        sync_v2.run_import(sync_v2.ROOT / 'tests/fixtures/workout.json',
                           sync_v2.ROOT / 'tests/fixtures/catalog.json', self.db)
        for module, key, value in [(coach_api, 'DB_FILE', self.db), (sync_workflow, 'DB_FILE', self.db),
                                   (self.app, 'SYNC_KEY', SYNC), (self.app, 'COACH_READ_KEY', COACH)]:
            self.enterContext(patch.object(module, key, value))
        self.network = self.enterContext(patch('requests.sessions.Session.request',
            side_effect=AssertionError('No live network allowed')))
        self.client = self.enterContext(TestClient(self.app.app))
        self.headers = {'X-Coach-Read-Key': COACH}

    def tearDown(self):
        self.network.assert_not_called()

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def test_missing_and_wrong_key_all_coach_routes(self):
        for path in ['/training/context', '/training/latest', '/training/next', '/training/exercise/359/history', '/training/exercises/search?query=bench']:
            for headers in [{}, {'X-Coach-Read-Key': 'wrong'}, {'X-Next-Set-Key': SYNC}, {'X-Coach-Read-Key': SYNC}]:
                with self.subTest(path=path, headers=list(headers)):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 401)

    def test_unset_or_reused_key_fails_closed(self):
        for value in ['', SYNC]:
            with patch.object(self.app, 'COACH_READ_KEY', value):
                self.assertEqual(self.get('/training/context').status_code, 503)

    def test_coach_cannot_write_or_read_snapshots(self):
        for headers in [self.headers, {'X-Next-Set-Key': COACH}]:
            self.assertEqual(self.client.post('/sync/workout/100', headers=headers).status_code, 401)
            self.assertEqual(self.client.post('/token', headers=headers, json={'token': 'fake', 'expires': 9999999999}).status_code, 401)
        self.assertEqual(self.get('/data/raw/workout-100/file.json').status_code, 404)

    def test_context_compact(self):
        response = self.get('/training/context?history_sessions=2')
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['preferred_weight_unit'], 'lb')
        self.assertEqual(body['next_session']['session']['day'], 4)
        self.assertLess(len(response.content), 40000)
        for forbidden in [SYNC, COACH, 'workout_json', 'catalog_json', str(self.db), 'Authorization']:
            self.assertNotIn(forbidden, response.text)

    def test_search_query_validation(self):
        for query in ('', ' ', '%%', 'x' * 101):
            response = self.client.get('/training/exercises/search', params={'query':query}, headers=self.headers)
            self.assertEqual(response.status_code, 422)
        response = self.get('/training/exercises/search?query=bench')
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json()['matches']), 10)

    def test_latest(self):
        body = self.get('/training/latest').json()
        self.assertEqual(body['session']['day'], 3)
        self.assertEqual(body['session']['completion'], 'inferred_complete')
        self.assertEqual(len(body['exercises']), 1)

    def test_next_includes_unperformed_prescription_and_sets(self):
        body = self.get('/training/next').json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual((body['session']['week'], body['session']['day']), (1, 4))
        self.assertTrue(body['session']['exercises'])
        self.assertTrue(body['session']['exercises'][0]['target_sets'])
        self.assertEqual(body['session']['exercises'][0]['selected_exercises'], [])

    def test_substituted_history_and_unknown_reps(self):
        body = self.get('/training/exercise/359/history').json()
        exercise = body['sessions'][0]['exercises'][0]
        self.assertEqual(exercise['prescribed_exercise_id'], 101)
        self.assertEqual(exercise['selected_exercise_id'], 359)
        self.assertEqual(exercise['selected_name'], 'Test Cable Press')
        self.assertTrue(exercise['substitution'])
        for entry in exercise['sets']:
            self.assertEqual(entry['logged_reps_raw'], '0')
            self.assertIsNone(entry['actual_reps'])
            self.assertEqual(entry['target_reps'], 10)
        self.assertEqual(self.get('/training/exercise/101/history').json()['sessions'], [])

    def test_reads_do_not_mutate_database(self):
        before = self.db.read_bytes()
        for path in ['/training/context', '/training/latest', '/training/next', '/training/exercise/2/history', '/training/summary']:
            self.assertEqual(self.get(path).status_code, 200)
        self.assertEqual(self.db.read_bytes(), before)
        with coach_api.database() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM performed_sets')

    def test_summary_both_existing_sync_and_coach_auth(self):
        self.assertEqual(self.get('/training/summary').status_code, 200)
        self.assertEqual(self.client.get('/training/summary', headers={'X-Next-Set-Key': SYNC}).status_code, 200)

    def test_empty_database(self):
        empty = self.db.parent / 'empty.db'
        conn = sqlite3.connect(empty)
        sync_v2.create_schema(conn)
        conn.close()
        with patch.object(coach_api, 'DB_FILE', empty):
            self.assertEqual(self.get('/training/latest').json()['status'], 'no_sessions')
            self.assertEqual(self.get('/training/next').json()['status'], 'no_prescriptions')
            self.assertEqual(self.get('/training/context').status_code, 200)

    def test_no_next_session(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE prescribed_exercises SET source_present=0 WHERE week>1 OR day>3')
        self.assertEqual(self.get('/training/next').json()['status'], 'no_next_session')

    def test_incomplete_latest_does_not_anchor_next(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE performed_exercises SET completed=0 WHERE session_id=3')
        body = self.get('/training/next').json()
        self.assertEqual(body['latest_completed_session']['day'], 1)
        self.assertEqual(body['session']['day'], 2)

    def test_ambiguous_instances(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO workout_instances VALUES(99,13,'now','now')")
            conn.execute('''INSERT INTO prescribed_exercises
                SELECT 99,master_workout_exercise_id,prescribed_exercise_id,week,day,display_order,
                prescribed_sets,prescribed_reps,rep_range,prescribed_rir,intensity_technique,note,tempo,rest,
                source_present,first_seen_at,last_seen_at,last_import_id FROM prescribed_exercises LIMIT 1''')
        self.assertEqual(self.get('/training/next').json()['status'], 'ambiguous_workout')
        self.assertEqual(self.get('/training/next?workout_id=100').json()['status'], 'ok')

    def test_invalid_ids_and_history_limits(self):
        for id in ['abc', '0', '-1', str(2**63)]:
            self.assertEqual(self.get('/training/exercise/'+id+'/history').status_code, 422)
        self.assertEqual(self.get('/training/exercise/999999/history').status_code, 404)
        for path in ['/training/context', '/training/exercise/2/history']:
            for limit in ['0', '6', '-1', 'oops']:
                self.assertEqual(self.get(path+'?history_sessions='+limit).status_code, 422)

    def test_missing_db_sanitized_and_not_created(self):
        missing = self.db.parent / 'absent.db'
        with patch.object(coach_api, 'DB_FILE', missing):
            response = self.get('/training/latest')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(missing), response.text)
        self.assertFalse(missing.exists())

    def test_no_completed_session_does_not_guess(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE performed_exercises SET completed=0')
        result = self.get('/training/next').json()
        self.assertEqual(result['status'], 'no_completed_session')
        self.assertIsNone(result['session'])

    def test_ambiguous_latest_date_does_not_guess(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE workout_sessions SET workout_date='2025-01-03T12:00:00'")
        self.assertEqual(self.get('/training/latest').json()['status'], 'ambiguous_latest')
