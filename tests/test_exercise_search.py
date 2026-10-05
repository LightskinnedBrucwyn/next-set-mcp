import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import coach_api


class ExerciseSearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'catalog.db'
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE exercise_catalog (exercise_id INTEGER PRIMARY KEY, name TEXT)')
            conn.execute('PRAGMA user_version=2')
            conn.executemany('INSERT INTO exercise_catalog VALUES (?,?)', [
                (1, 'Bench Press'), (2, 'Incline Bench Press'), (3, 'Dumbbell Bench Press'),
                (4, 'Bench Row'), (5, 'Leg Press'),
                *[(100+i, f'Cable Exercise {i}') for i in range(12)],
            ])
        conn.close()
        self.enterContext(patch.object(coach_api, 'DB_FILE', self.path))

    def search(self, query):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        with coach_api.database() as conn:
            self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0], 1)
            result = coach_api.search_exercises(conn, query)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM exercise_catalog")
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        return result

    def test_case_partial_and_reordered_words_preserve_canonical_names(self):
        for query in ('BENCH press', '  bench   press  ', 'press bench', 'ben pre', 'bench-press'):
            result = self.search(query)
            self.assertEqual({r['exercise_id'] for r in result['matches']}, {1, 2, 3})
            self.assertFalse(result['has_more'])
        self.assertEqual(self.search('bench press')['matches'][0], {'exercise_id':1, 'name':'Bench Press'})

    def test_bound_and_no_match(self):
        result = self.search('cable')
        self.assertEqual(len(result['matches']), 10)
        self.assertTrue(result['has_more'])
        self.assertEqual(self.search('notarealexercisename'), {'matches':[], 'has_more':False})

    def test_sql_input_is_literal_not_injection(self):
        self.assertEqual(self.search("bench' OR 1=1 --")['matches'], [])
        self.assertEqual(len(self.search('bench')['matches']), 4)
