"""Create an entirely synthetic training database without any source adapter."""
import argparse
import hashlib
from pathlib import Path
import sqlite3


def create_demo(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting a real training database.
    with destination.open('xb'):
        pass
    conn = sqlite3.connect(destination)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((Path(__file__).resolve().parents[1] / 'schema_v2.sql').read_text(encoding='utf-8'))
        with conn:
            def insert(table, **values):
                # Identifiers below are developer constants, never user input.
                columns = ','.join(values)
                placeholders = ','.join('?' for _ in values)
                conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(values.values()))
            stamp = '2025-01-01T12:00:00'
            audit = dict(first_seen_at=stamp, last_seen_at=stamp, last_import_id=1)
            presence = dict(source_present=1, **audit)
            insert('programs', master_workout_id=1)
            insert('workout_instances', workout_id=1, master_workout_id=1, first_seen_at=stamp, last_seen_at=stamp)
            raw = b'{"synthetic":true}'
            digest = hashlib.sha256(raw).hexdigest()
            insert('import_snapshots', import_id=1, imported_at=stamp, workout_id=1, master_workout_id=1,
                   workout_path='synthetic-demo', catalog_path='synthetic-demo', snapshot_mode='full',
                   assumed_weight_unit='lb', workout_sha256=digest, catalog_sha256=digest,
                   workout_json=raw, catalog_json=raw)
            for exercise_id, name in [(1, 'Demo Bench Press'), (2, 'Demo Squat')]:
                insert('exercise_catalog', exercise_id=exercise_id, name=name, **audit)
                insert('prescribed_exercises', workout_id=1, master_workout_exercise_id=exercise_id,
                       prescribed_exercise_id=exercise_id, week=1, day=exercise_id, display_order=1,
                       prescribed_sets=1, prescribed_reps=8, prescribed_rir='2', **presence)
                insert('prescribed_sets', workout_id=1, master_workout_exercise_set_id=exercise_id,
                       master_workout_exercise_id=exercise_id, set_order=1, target_reps=8, target_rir='2', **presence)
            insert('workout_sessions', session_id=1, workout_id=1, session_key='demo-day-one', workout_date=stamp, week=1, day=1)
            insert('performed_exercises', workout_id=1, workout_exercise_id=1, master_workout_exercise_id=1,
                   session_id=1, selected_exercise_id=1, completed=1, **presence)
            insert('performed_sets', workout_id=1, set_id=1, workout_exercise_id=1, master_workout_exercise_id=1,
                   master_workout_exercise_set_id=1, set_order=1, logged_reps_raw='8', actual_reps=8,
                   weight_raw='45', source_weight_unit='lb', weight_kg=45/2.2046226218487757,
                   weight_lb=45, completed=1, is_pr=0, **presence)
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    create_demo(args.db)
    print('Created synthetic training data: one completed session and one future prescription.')
