"""Bounded, read-only coaching views. No raw snapshots or free-form SQL API."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import re

from fastapi import HTTPException
import storage

DB_FILE = storage.DEFAULT_DB_FILE
MAX_EXERCISES = 40
MAX_SETS = 40


@contextmanager
def database():
    conn = None
    try:
        conn = sqlite3.connect(Path(DB_FILE).resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        if conn.execute('PRAGMA user_version').fetchone()[0] != 2:
            raise HTTPException(503, 'Training database schema is unavailable.')
        yield conn
    except sqlite3.Error:
        raise HTTPException(503, 'Training database is unavailable.') from None
    finally:
        if conn is not None:
            conn.close()


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def search_exercises(conn, query):
    # Literal, order-independent partial words; no wildcard expansion or invented aliases.
    terms = re.findall(r'\w+', query.lower(), flags=re.UNICODE)
    if not terms:
        raise HTTPException(422, 'Search query must contain a word or number.')
    phrase = ' '.join(terms)
    predicates = ' AND '.join('instr(lower(name), ?) > 0' for _ in terms)
    matches = rows(conn, f'''SELECT exercise_id,name FROM exercise_catalog
        WHERE {predicates}
        ORDER BY CASE WHEN lower(name)=? THEN 0
                      WHEN instr(lower(name), ?)>0 THEN 1 ELSE 2 END,
                 name COLLATE NOCASE,exercise_id LIMIT 11''', (*terms, phrase, phrase))
    return {'matches': matches[:10], 'has_more': len(matches) > 10}


def sessions(conn, workout_id=None):
    # Only groupings with source-present performance are current sessions.
    return rows(conn, '''SELECT s.session_id,s.workout_id,s.workout_date,s.week,s.day,
        w.master_workout_id,
        (SELECT COUNT(*) FROM prescribed_exercises p WHERE p.workout_id=s.workout_id
          AND p.week=s.week AND p.day=s.day AND p.source_present=1) prescribed_count,
        (SELECT COUNT(DISTINCT e.master_workout_exercise_id) FROM performed_exercises e
          JOIN prescribed_exercises p USING(workout_id,master_workout_exercise_id)
          WHERE e.session_id=s.session_id AND e.source_present=1 AND e.completed=1
          AND p.source_present=1 AND p.week=s.week AND p.day=s.day) completed_count
        FROM workout_sessions s JOIN workout_instances w USING(workout_id)
        WHERE EXISTS(SELECT 1 FROM performed_exercises e WHERE e.session_id=s.session_id AND e.source_present=1)
        AND (? IS NULL OR s.workout_id=?)
        ORDER BY s.workout_date DESC,s.session_id DESC''', (workout_id, workout_id))


def session_meta(session):
    result = dict(session)
    result['completion'] = ('inferred_complete' if session['prescribed_count'] > 0 and
                            session['completed_count'] == session['prescribed_count'] else 'incomplete_or_unknown')
    return result


def latest_selection(conn, workout_id=None, completed=False):
    candidates = [session_meta(s) for s in sessions(conn, workout_id)]
    if completed:
        candidates = [s for s in candidates if s['completion'] == 'inferred_complete']
    if not candidates:
        return {'status': 'no_completed_session' if completed else 'no_sessions', 'session': None}
    # Dates are source wall times. Do not invent chronological equivalence across offsets.
    if any(s['workout_date'] is None for s in candidates):
        return {'status': 'ambiguous_dates', 'session': None, 'candidate_sessions': candidates[:10]}
    parsed = [datetime.fromisoformat(s['workout_date']) for s in candidates]
    if len({p.utcoffset() for p in parsed}) != 1:
        return {'status': 'ambiguous_timezones', 'session': None, 'candidate_sessions': candidates[:10]}
    newest = max(parsed)
    top = [s for s, date in zip(candidates, parsed) if date == newest]
    if len(top) != 1:
        return {'status': 'ambiguous_latest', 'session': None, 'candidate_sessions': top[:10]}
    return {'status': 'ok', 'session': top[0]}


def prescription(conn, workout_id, week, day):
    exercises = rows(conn, '''SELECT p.master_workout_exercise_id,p.prescribed_exercise_id,
        c.name prescribed_name,p.prescribed_sets,p.prescribed_reps,p.rep_range,p.prescribed_rir,
        p.display_order FROM prescribed_exercises p LEFT JOIN exercise_catalog c
        ON c.exercise_id=p.prescribed_exercise_id
        WHERE p.workout_id=? AND p.week=? AND p.day=? AND p.source_present=1
        ORDER BY p.display_order,p.master_workout_exercise_id LIMIT ?''', (workout_id, week, day, MAX_EXERCISES+1))
    if len(exercises) > MAX_EXERCISES:
        raise HTTPException(422, 'Session exceeds the supported exercise limit.')
    for ex in exercises:
        ex['target_sets'] = rows(conn, '''SELECT master_workout_exercise_set_id,set_order,target_reps,
            target_rir,is_to_failure,is_child FROM prescribed_sets WHERE workout_id=?
            AND master_workout_exercise_id=? AND source_present=1 ORDER BY set_order LIMIT ?''',
            (workout_id, ex['master_workout_exercise_id'], MAX_SETS+1))
        if len(ex['target_sets']) > MAX_SETS:
            raise HTTPException(422, 'Exercise exceeds the supported set limit.')
        ex['selected_exercises'] = rows(conn, '''SELECT e.workout_exercise_id,e.selected_exercise_id,
            c.name selected_name,e.completed,e.session_id FROM performed_exercises e
            LEFT JOIN exercise_catalog c ON c.exercise_id=e.selected_exercise_id
            WHERE e.workout_id=? AND e.master_workout_exercise_id=? AND e.source_present=1
            ORDER BY e.workout_exercise_id LIMIT 10''', (workout_id, ex['master_workout_exercise_id']))
    return exercises


def performance(conn, session_id, exercise_id=None):
    exercises = rows(conn, '''SELECT e.workout_id,e.workout_exercise_id,e.master_workout_exercise_id,
        p.prescribed_exercise_id,pc.name prescribed_name,e.selected_exercise_id,sc.name selected_name,
        COALESCE(e.selected_exercise_id,p.prescribed_exercise_id) performed_exercise_id,
        e.completed,p.prescribed_sets,p.prescribed_reps,p.rep_range,p.prescribed_rir
        FROM performed_exercises e JOIN prescribed_exercises p USING(workout_id,master_workout_exercise_id)
        LEFT JOIN exercise_catalog pc ON pc.exercise_id=p.prescribed_exercise_id
        LEFT JOIN exercise_catalog sc ON sc.exercise_id=e.selected_exercise_id
        WHERE e.session_id=? AND e.source_present=1
        AND (? IS NULL OR COALESCE(e.selected_exercise_id,p.prescribed_exercise_id)=?)
        ORDER BY p.display_order,e.workout_exercise_id LIMIT ?''', (session_id, exercise_id, exercise_id, MAX_EXERCISES+1))
    if len(exercises) > MAX_EXERCISES:
        raise HTTPException(422, 'Session exceeds the supported exercise limit.')
    for ex in exercises:
        ex['substitution'] = ex['selected_exercise_id'] is not None and ex['selected_exercise_id'] != ex['prescribed_exercise_id']
        ex['sets'] = rows(conn, '''SELECT s.set_id,s.set_order,s.master_workout_exercise_set_id,
            p.target_reps,p.target_rir,s.logged_reps_raw,s.actual_reps,s.weight_raw,
            s.source_weight_unit,s.weight_kg,s.weight_lb,s.completed,s.is_pr
            FROM performed_sets s LEFT JOIN prescribed_sets p
            USING(workout_id,master_workout_exercise_set_id,master_workout_exercise_id)
            WHERE s.workout_id=? AND s.workout_exercise_id=? AND s.source_present=1
            ORDER BY s.set_order,s.set_id LIMIT ?''', (ex['workout_id'], ex['workout_exercise_id'], MAX_SETS+1))
        if len(ex['sets']) > MAX_SETS:
            raise HTTPException(422, 'Exercise exceeds the supported set limit.')
        for entry in ex['sets']:
            entry['weight_lb_display'] = None if entry['weight_lb'] is None else round(entry['weight_lb'], 2)
    return exercises


def latest(conn, workout_id=None):
    result = latest_selection(conn, workout_id)
    result['exercises'] = performance(conn, result['session']['session_id']) if result['session'] else []
    return result


def next_session(conn, workout_id=None):
    instances = rows(conn, '''SELECT DISTINCT w.workout_id,w.master_workout_id FROM workout_instances w
        JOIN prescribed_exercises p USING(workout_id) WHERE p.source_present=1
        AND (? IS NULL OR w.workout_id=?) ORDER BY w.workout_id''', (workout_id, workout_id))
    if len(instances) != 1:
        return {'status': 'ambiguous_workout' if instances else 'no_prescriptions',
                'session': None, 'candidate_workouts': instances[:10]}
    instance = instances[0]
    anchor = latest_selection(conn, instance['workout_id'], completed=True)
    if not anchor['session']:
        return {'status': anchor['status'], 'session': None, 'latest_completed_session': None}
    previous = anchor['session']
    if previous['week'] is None or previous['day'] is None:
        return {'status': 'unknown_program_position', 'session': None}
    options = rows(conn, '''SELECT DISTINCT week,day FROM prescribed_exercises
        WHERE workout_id=? AND source_present=1 AND week IS NOT NULL AND day IS NOT NULL
        AND (week>? OR (week=? AND day>?)) ORDER BY week,day LIMIT 1''',
        (instance['workout_id'], previous['week'], previous['week'], previous['day']))
    if not options:
        return {'status': 'no_next_session', 'session': None, 'latest_completed_session': previous}
    chosen = dict(**instance, **options[0])
    chosen['exercises'] = prescription(conn, chosen['workout_id'], chosen['week'], chosen['day'])
    return {'status': 'ok', 'session': chosen, 'latest_completed_session': previous,
            'basis': 'First prescribed week/day after latest inferred-complete session; not a progression recommendation.'}


def history(conn, exercise_id, limit):
    if conn.execute('SELECT 1 FROM exercise_catalog WHERE exercise_id=?', (exercise_id,)).fetchone() is None:
        if conn.execute('''SELECT 1 FROM performed_exercises e JOIN prescribed_exercises p
            USING(workout_id,master_workout_exercise_id) WHERE
            COALESCE(e.selected_exercise_id,p.prescribed_exercise_id)=? LIMIT 1''', (exercise_id,)).fetchone() is None:
            raise HTTPException(404, 'Exercise is not known.')
    selected = rows(conn, '''SELECT DISTINCT s.session_id,s.workout_id,s.workout_date,s.week,s.day
        FROM workout_sessions s JOIN performed_exercises e USING(session_id,workout_id)
        JOIN prescribed_exercises p USING(workout_id,master_workout_exercise_id)
        WHERE e.source_present=1 AND COALESCE(e.selected_exercise_id,p.prescribed_exercise_id)=?
        ORDER BY s.workout_date DESC,s.session_id DESC LIMIT ?''', (exercise_id, limit+1))
    return {'exercise_id': exercise_id, 'has_more': len(selected)>limit,
            'sessions': [dict(**s, exercises=performance(conn, s['session_id'], exercise_id)) for s in selected[:limit]]}


def context(conn, limit, workout_id=None):
    upcoming = next_session(conn, workout_id)
    identifiers = set()
    if upcoming['session']:
        for ex in upcoming['session']['exercises']:
            identifiers.add(ex['prescribed_exercise_id'])
            identifiers.update(e['selected_exercise_id'] for e in ex['selected_exercises'] if e['selected_exercise_id'] is not None)
    recent = [history(conn, exercise_id, limit) for exercise_id in sorted(identifiers)[:12]
              if conn.execute('SELECT 1 FROM exercise_catalog WHERE exercise_id=?', (exercise_id,)).fetchone()]
    last = latest_selection(conn, workout_id)
    completed = latest_selection(conn, workout_id, completed=True)
    program_session = upcoming['session'] or last['session']
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'preferred_weight_unit': 'lb',
            'latest_session': last, 'latest_completed_session': completed,
            'next_session': upcoming, 'recent_performance': recent,
            'history_exercises_truncated': len(identifiers)>12,
            'program': {'workout_id': program_session['workout_id'],
                        'master_workout_id': program_session['master_workout_id']} if program_session else None}
