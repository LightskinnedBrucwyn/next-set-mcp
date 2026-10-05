import copy
import json
from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from adapters.biolayne import sync_v2 as sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        sync.create_schema(self.conn)
        self.catalog = {'2': {'id': 2, 'name': 'Bench Press'}, '3': {'id': 3, 'name': 'DB Press'}}
        self.rows = [{
            'id': 10, 'masterWorkoutId': 13, 'masterExerciseId': 2,
            'week': 1, 'day': 1, 'sets': 2, 'reps': 8, 'rir': '2',
            'masterWorkoutExerciseSets': {
                '20': {'id': 20, 'masterWorkoutExerciseId': 10, 'reps': 8, 'rir': '2', 'isToFailure': False},
                '21': {'id': 21, 'masterWorkoutExerciseId': 10, 'reps': 6, 'note': 'Backoff', 'isChild': True},
            },
            'workoutExercise': {
                'id': 30, 'masterWorkoutExerciseId': 10, 'workoutId': 100,
                'selectedMasterExerciseId': 3, 'date': '2026-09-20T00:00:00', 'isCompleted': True,
                'workoutExerciseSets': {'40': {
                    'id': 40, 'workoutExerciseId': 30, 'masterWorkoutExerciseSetId': 20,
                    'reps': 0, 'weight': '61.234920000', 'isCompleted': True, 'isPr': False,
                }},
            },
        }]

    def ingest(self, rows=None, **options):
        return sync.import_snapshot(self.conn, json.dumps(self.rows if rows is None else rows).encode(),
                                    json.dumps(self.catalog).encode(), **options)

    def count(self, table):
        return self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def set_value(self, rows, key, value):
        rows[0]['workoutExercise']['workoutExerciseSets']['40'][key] = value

    def test_clean_import_and_version(self):
        self.ingest()
        self.assertEqual(self.count('prescribed_exercises'), 1)
        self.assertEqual(self.count('performed_sets'), 1)
        self.assertEqual(self.conn.execute('PRAGMA user_version').fetchone(), (2,))
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_repeat_updates_entities_records_each_snapshot(self):
        self.ingest()
        self.ingest()
        self.assertEqual(self.count('performed_sets'), 1)
        self.assertEqual(self.count('prescribed_sets'), 2)
        self.assertEqual(self.count('workout_sessions'), 1)
        self.assertEqual(self.count('import_snapshots'), 2)

    def test_two_instances_same_source_ids_isolated(self):
        self.ingest(mode='full')
        other = copy.deepcopy(self.rows)
        other[0]['workoutExercise'].update(workoutId=101, date='2026-10-01', selectedMasterExerciseId=2)
        self.set_value(other, 'weight', 100)
        self.ingest(other, mode='full')
        self.assertEqual(self.count('performed_sets'), 2)
        self.assertEqual(self.conn.execute('SELECT workout_id, selected_exercise_id FROM performed_exercises ORDER BY workout_id').fetchall(), [(100, 3), (101, 2)])
        self.assertEqual(self.conn.execute('SELECT source_present FROM performed_sets WHERE workout_id=100').fetchone(), (1,))

    def test_substitution_and_prescription_separate(self):
        self.ingest()
        self.assertEqual(self.conn.execute('SELECT prescribed_exercise_id FROM prescribed_exercises').fetchone(), (2,))
        self.assertEqual(self.conn.execute('SELECT selected_exercise_id FROM performed_exercises').fetchone(), (3,))

    def test_raw_zero_reps_not_inferred(self):
        self.ingest()
        self.assertEqual(self.conn.execute('SELECT logged_reps_raw, actual_reps FROM performed_sets').fetchone(), ('0', None))

    def test_raw_precision_conversion(self):
        self.ingest()
        raw, kg, lb = self.conn.execute('SELECT weight_raw, weight_kg, weight_lb FROM performed_sets').fetchone()
        self.assertEqual(raw, '61.234920000')
        self.assertAlmostEqual(kg, 61.23492)
        self.assertAlmostEqual(lb, 134.99988987910004)

    def test_numeric_decimal_lexeme_saved_in_snapshot(self):
        raw = json.dumps(self.rows).replace('"61.234920000"', '61.234920000').encode()
        sync.import_snapshot(self.conn, raw, json.dumps(self.catalog).encode())
        self.assertEqual(self.conn.execute('SELECT weight_raw FROM performed_sets').fetchone(), ('61.234920000',))
        self.assertEqual(self.conn.execute('SELECT workout_json FROM import_snapshots').fetchone(), (raw,))

    def test_lb_assumption(self):
        self.set_value(self.rows, 'weight', 135)
        self.ingest(weight_unit='lb')
        kg, lb, unit = self.conn.execute('SELECT weight_kg, weight_lb, source_weight_unit FROM performed_sets').fetchone()
        self.assertAlmostEqual(kg, 61.23492, places=4)
        self.assertEqual((lb, unit), (135, 'lb'))

    def test_zero_and_missing_weight(self):
        self.set_value(self.rows, 'weight', 0)
        self.ingest()
        self.assertEqual(self.conn.execute('SELECT weight_raw, weight_kg, weight_lb FROM performed_sets').fetchone(), ('0', 0.0, 0.0))
        self.set_value(self.rows, 'weight', None)
        self.ingest()
        self.assertEqual(self.conn.execute('SELECT weight_raw, weight_kg FROM performed_sets').fetchone(), (None, None))

    def test_invalid_numeric_and_flags_rollback(self):
        self.ingest()
        before = list(self.conn.iterdump())
        for key, value in [('reps', 7.5), ('reps', -1), ('reps', True), ('weight', -1),
                           ('weight', 'NaN'), ('weight', 'Infinity'), ('weight', '1e400'),
                           ('isCompleted', 'false'), ('isPr', 1), ('id', 2**63)]:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.rows)
                self.set_value(changed, key, value)
                with self.assertRaises(ValueError):
                    self.ingest(changed, mode='full')
                self.assertEqual(list(self.conn.iterdump()), before)

    def test_nan_infinity_duplicate_json_key_rejected(self):
        for raw in [b'[NaN]', b'[Infinity]', b'[-Infinity]', b'{"x":1,"x":2}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sync.parse_json(raw)

    def test_late_failure_rolls_back_catalog_snapshot_and_missing_flags(self):
        self.ingest()
        before = list(self.conn.iterdump())
        changed = copy.deepcopy(self.rows)
        changed.append(copy.deepcopy(changed[0]))
        changed[1]['id'] = 11
        changed[1]['sets'] = 2.5
        self.catalog['2']['name'] = 'Changed'
        with self.assertRaises(ValueError):
            self.ingest(changed, mode='full')
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_full_missing_and_reappearing_scoped_to_instance(self):
        self.ingest(mode='full')
        other = copy.deepcopy(self.rows)
        other[0]['workoutExercise']['workoutId'] = 101
        self.ingest(other, mode='full')
        changed = copy.deepcopy(self.rows)
        changed[0]['workoutExercise']['workoutExerciseSets'] = {}
        self.ingest(changed, mode='full')
        self.assertEqual(self.conn.execute('SELECT workout_id, source_present FROM performed_sets ORDER BY workout_id').fetchall(), [(100, 0), (101, 1)])
        self.ingest(mode='full')
        self.assertEqual(self.conn.execute('SELECT source_present FROM performed_sets WHERE workout_id=100').fetchone(), (1,))

    def test_partial_omission_preserves_entities(self):
        self.ingest(mode='full')
        changed = copy.deepcopy(self.rows)
        changed[0]['masterWorkoutExerciseSets'] = {}
        changed[0]['workoutExercise']['workoutExerciseSets'] = {}
        self.ingest(changed, mode='partial')
        self.assertEqual(self.conn.execute('SELECT source_present FROM performed_sets').fetchone(), (1,))
        self.assertEqual(self.conn.execute('SELECT SUM(source_present) FROM prescribed_sets').fetchone(), (2,))

    def test_empty_full_requires_explicit_identity_and_marks_only_scope(self):
        self.ingest()
        with self.assertRaises(ValueError):
            self.ingest([], mode='full')
        self.ingest([], mode='full', workout_id=100, master_workout_id=13)
        self.assertEqual(self.conn.execute('SELECT source_present FROM performed_exercises').fetchone(), (0,))
        self.assertEqual(self.count('performed_sets'), 1)

    def test_unperformed_prescribed_sets_retained(self):
        self.rows[0]['workoutExercise'] = None
        self.ingest(workout_id=100)
        self.assertEqual(self.count('prescribed_sets'), 2)
        self.assertEqual(self.count('performed_sets'), 0)
        self.assertEqual(self.conn.execute('SELECT target_reps, note, is_child FROM prescribed_sets WHERE master_workout_exercise_set_id=21').fetchone(), (6, 'Backoff', 1))

    def test_malformed_containers_fail(self):
        for value in [[], False, '', 0]:
            changed = copy.deepcopy(self.rows)
            changed[0]['masterWorkoutExerciseSets'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ingest(changed)
        self.assertEqual(self.count('import_snapshots'), 0)

    def test_duplicate_row_id_rejected(self):
        with self.assertRaises(ValueError):
            self.ingest(self.rows + copy.deepcopy(self.rows))

    def test_id_bounds_and_fraction_rejected(self):
        for value in [0, -1, 2**63, 1.5, True, 'Infinity']:
            changed = copy.deepcopy(self.rows)
            changed[0]['id'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ingest(changed)

    def test_parent_mismatch_rejected(self):
        for key in ['workoutExerciseId', 'masterWorkoutExerciseSetId']:
            changed = copy.deepcopy(self.rows)
            self.set_value(changed, key, 999)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ingest(changed)

    def test_conflicting_explicit_identity_rejected(self):
        with self.assertRaises(ValueError):
            self.ingest(workout_id=101)

    def test_sessions_keep_different_dates_in_one_workout(self):
        second = copy.deepcopy(self.rows[0])
        second['id'] = 11
        second['day'] = 2
        second['masterWorkoutExerciseSets'] = {}
        second['workoutExercise'].update(id=31, masterWorkoutExerciseId=11, date='2026-09-21', workoutExerciseSets={})
        self.ingest(self.rows + [second])
        self.assertEqual(self.count('workout_instances'), 1)
        self.assertEqual(self.count('workout_sessions'), 2)

    def test_edited_value_remains_in_previous_snapshot(self):
        self.ingest()
        self.set_value(self.rows, 'reps', 9)
        self.ingest()
        old = self.conn.execute('SELECT workout_json FROM import_snapshots ORDER BY import_id LIMIT 1').fetchone()[0]
        self.assertEqual(json.loads(old)[0]['workoutExercise']['workoutExerciseSets']['40']['reps'], 0)
        self.assertEqual(self.conn.execute('SELECT actual_reps FROM performed_sets').fetchone(), (9,))

    def test_reject_legacy_db_without_changes(self):
        other = sqlite3.connect(':memory:')
        self.addCleanup(other.close)
        other.execute('CREATE TABLE program_exercises(id INTEGER)')
        before = list(other.iterdump())
        with self.assertRaises(ValueError):
            sync.create_schema(other)
        self.assertEqual(list(other.iterdump()), before)

    def test_production_path_rejected(self):
        with self.assertRaises(ValueError):
            sync.run_import(Path('unused'), Path('unused'), sync.ROOT / 'data/training.db')

    def test_synthetic_json_in_temporary_database(self):
        sample_db = Path(self.temp.name) / 'sample-v2.db'
        workout = sync.ROOT / 'tests/fixtures/workout.json'
        catalog = sync.ROOT / 'tests/fixtures/catalog.json'
        result = sync.run_import(workout, catalog, sample_db, mode='full')
        counts = result['database_totals']
        self.assertEqual(counts['exercise_catalog'], 3)
        self.assertEqual(counts['prescribed_exercises'], 4)
        self.assertEqual(counts['prescribed_sets'], 8)
        self.assertEqual(counts['performed_sets'], 6)
        second = sync.run_import(workout, catalog, sample_db, mode='full')
        self.assertEqual(second['database_totals']['performed_sets'], 6)
        self.assertEqual(second['database_totals']['import_snapshots'], 2)
        with closing(sqlite3.connect(sample_db)) as check:
            self.assertEqual(check.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(check.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(check.execute("SELECT COUNT(*) FROM performed_sets WHERE logged_reps_raw='0' AND actual_reps IS NULL AND completed=1").fetchone(), (2,))

    def test_cli_explicit_inputs_work_outside_project_directory(self):
        output_db = Path(self.temp.name) / 'cli.db'
        result = subprocess.run(
            [sys.executable, str(sync.ROOT / 'sync.py'), '--workout', str(sync.ROOT / 'tests/fixtures/workout.json'), '--master', str(sync.ROOT / 'tests/fixtures/catalog.json'), '--db', str(output_db)],
            cwd=self.temp.name, capture_output=True, text=True, check=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report['database_totals']['performed_sets'], 6)
        self.assertEqual(report['database'], str(output_db.resolve()))


if __name__ == '__main__':
    unittest.main()
