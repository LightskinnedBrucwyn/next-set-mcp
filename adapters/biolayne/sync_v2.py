"""Offline, transactional BioLayne snapshot importer. No credentials or network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from storage import DEFAULT_DB_FILE
KG_TO_LB = Decimal("2.2046226218487757")
SQLITE_MIN, SQLITE_MAX = -(2**63), 2**63 - 1
STATE_TABLES = ("prescribed_exercises", "prescribed_sets", "performed_exercises", "performed_sets")


def object_value(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def optional_object(value, label):
    return {} if value is None else object_value(value, label)


def number(value, label):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValueError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{label} must be numeric") from None
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def integer(value, label, *, required=False, minimum=0):
    result = number(value, label)
    if result is None:
        if required:
            raise ValueError(f"{label} is required")
        return None
    if result != result.to_integral_value() or not SQLITE_MIN <= result <= SQLITE_MAX:
        raise ValueError(f"{label} must be an exact SQLite integer")
    if result < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return int(result)


def identity(value, label):
    return integer(value, label, required=True, minimum=1)


def boolean(value, label):
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{label} must be a JSON boolean")
    return int(value)


def text_value(value, label):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    return value


def scalar_text(value, label):
    if value is None or isinstance(value, str):
        return value
    number(value, label)
    return str(value)


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are forbidden")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw.decode("utf-8-sig"), parse_float=Decimal,
                      parse_constant=reject_constant, object_pairs_hook=unique_object)


def create_schema(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if version == 2:
        if "schema_metadata" not in tables or conn.execute(
            "SELECT version FROM schema_metadata WHERE singleton=1"
        ).fetchone() != (2,):
            raise ValueError("Inconsistent v2 schema metadata")
        required = set(STATE_TABLES) | {"programs", "workout_instances", "import_snapshots",
                                      "workout_sessions", "exercise_catalog"}
        if not required <= tables:
            raise ValueError("Incomplete v2 schema")
        return
    if version or tables:
        raise ValueError("Existing database is not v2; use a NEW --db file. No migration was attempted.")
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + (ROOT / "schema_v2.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
    except Exception:
        conn.rollback()
        raise


def upsert(conn, table, keys, values):
    # Names are exclusively internal constants, never JSON values.
    columns = tuple(values)
    updates = [c for c in columns if c not in keys and c != "first_seen_at"]
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
        f"ON CONFLICT({','.join(keys)}) DO UPDATE SET " +
        ','.join(f"{c}=excluded.{c}" for c in updates), tuple(values.values()),
    )


def check_parent(conn, table, id_column, entity_id, workout_id, parent_column, parent_id):
    row = conn.execute(
        f"SELECT {parent_column} FROM {table} WHERE workout_id=? AND {id_column}=?",
        (workout_id, entity_id),
    ).fetchone()
    if row is not None and row[0] != parent_id:
        raise ValueError(f"{table}: source ID changed parent")


def source_map(value, label):
    result = []
    seen = set()
    for key, raw in optional_object(value, label).items():
        item = object_value(raw, label)
        item_id = identity(item.get("id"), label + ".id")
        if identity(key, label + ".key") != item_id or item_id in seen:
            raise ValueError(f"{label}: mismatched key or duplicate ID")
        seen.add(item_id)
        result.append((item_id, item))
    return result


def scope(rows, workout_id, master_workout_id):
    if not isinstance(rows, list):
        raise ValueError("workout JSON must be an array")
    workouts, programs = set(), set()
    for raw in rows:
        item = object_value(raw, "workout row")
        programs.add(identity(item.get("masterWorkoutId"), "masterWorkoutId"))
        exercise = item.get("workoutExercise")
        if exercise is not None:
            exercise = object_value(exercise, "workoutExercise")
            workouts.add(identity(exercise.get("workoutId"), "workoutId"))
    if workout_id is not None:
        workouts.add(identity(workout_id, "--workout-id"))
    if master_workout_id is not None:
        programs.add(identity(master_workout_id, "--master-workout-id"))
    if len(workouts) != 1 or len(programs) != 1:
        raise ValueError("Exactly one workout and master workout are required; supply explicit IDs for empty/unstarted data")
    return workouts.pop(), programs.pop()


def import_snapshot(conn, workout_raw, catalog_raw, *, workout_path="<memory>",
                    catalog_path="<memory>", workout_id=None, master_workout_id=None,
                    mode="partial", weight_unit="kg"):
    if mode not in ("partial", "full") or weight_unit not in ("kg", "lb"):
        raise ValueError("Unsupported snapshot mode or assumed weight unit")
    rows, catalog = parse_json(workout_raw), object_value(parse_json(catalog_raw), "catalog")
    workout_id, master_id = scope(rows, workout_id, master_workout_id)
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    if conn.in_transaction:
        raise ValueError("Importer requires its own transaction")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT master_workout_id FROM workout_instances WHERE workout_id=?", (workout_id,)).fetchone()
        if previous is not None and previous[0] != master_id:
            raise ValueError("Workout changed master program")
        conn.execute("INSERT INTO programs VALUES (?) ON CONFLICT DO NOTHING", (master_id,))
        upsert(conn, "workout_instances", ("workout_id",), dict(workout_id=workout_id,
               master_workout_id=master_id, first_seen_at=now, last_seen_at=now))
        import_id = conn.execute(
            "INSERT INTO import_snapshots (imported_at, workout_id, master_workout_id, workout_path, catalog_path, "
            "snapshot_mode, assumed_weight_unit, workout_sha256, catalog_sha256, workout_json, catalog_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, workout_id, master_id, str(workout_path), str(catalog_path), mode, weight_unit,
             hashlib.sha256(workout_raw).hexdigest(), hashlib.sha256(catalog_raw).hexdigest(), workout_raw, catalog_raw),
        ).lastrowid
        audit = dict(first_seen_at=now, last_seen_at=now, last_import_id=import_id)
        state = dict(source_present=1, **audit)
        for exercise_id, item in source_map(catalog, "catalog"):
            name = text_value(item.get("name"), "exercise name")
            if not name or not name.strip():
                raise ValueError("Exercise name is required")
            upsert(conn, "exercise_catalog", ("exercise_id",), dict(exercise_id=exercise_id, name=name, **audit))
        if mode == "full":
            for table in STATE_TABLES:
                conn.execute(f"UPDATE {table} SET source_present=0 WHERE workout_id=?", (workout_id,))
        seen_exercises, seen_plans, seen_performed, seen_sets = set(), set(), set(), set()
        for item in rows:
            template_id = identity(item.get("id"), "prescribed exercise id")
            if template_id in seen_exercises:
                raise ValueError("Duplicate prescribed exercise ID")
            seen_exercises.add(template_id)
            week = integer(item.get("week"), "week", minimum=1)
            day = integer(item.get("day"), "day", minimum=1)
            upsert(conn, "prescribed_exercises", ("workout_id", "master_workout_exercise_id"), dict(
                workout_id=workout_id, master_workout_exercise_id=template_id,
                prescribed_exercise_id=identity(item.get("masterExerciseId"), "masterExerciseId"),
                week=week, day=day, display_order=integer(item.get("displayOrder"), "displayOrder"),
                prescribed_sets=integer(item.get("sets"), "sets"),
                prescribed_reps=integer(item.get("reps"), "prescribed reps"),
                rep_range=text_value(item.get("repRange"), "repRange"),
                prescribed_rir=scalar_text(item.get("rir"), "rir"),
                intensity_technique=text_value(item.get("intensityTechnique"), "intensityTechnique"),
                note=text_value(item.get("note"), "prescription note"),
                tempo=scalar_text(item.get("tempo"), "tempo"), rest=scalar_text(item.get("rest"), "rest"), **state))
            for plan_id, planned in source_map(item.get("masterWorkoutExerciseSets"), "planned sets"):
                if plan_id in seen_plans:
                    raise ValueError("Duplicate planned set ID")
                seen_plans.add(plan_id)
                if planned.get("masterWorkoutExerciseId") is not None and identity(
                    planned["masterWorkoutExerciseId"], "planned parent"
                ) != template_id:
                    raise ValueError("Planned set parent mismatch")
                check_parent(conn, "prescribed_sets", "master_workout_exercise_set_id", plan_id,
                             workout_id, "master_workout_exercise_id", template_id)
                upsert(conn, "prescribed_sets", ("workout_id", "master_workout_exercise_set_id"), dict(
                    workout_id=workout_id, master_workout_exercise_set_id=plan_id,
                    master_workout_exercise_id=template_id,
                    set_order=integer(planned.get("displayOrder"), "planned set order"),
                    target_reps=integer(planned.get("reps"), "target reps"),
                    target_rir=scalar_text(planned.get("rir"), "target RIR"),
                    note=text_value(planned.get("note"), "planned set note"),
                    is_to_failure=boolean(planned.get("isToFailure"), "isToFailure"),
                    is_child=boolean(planned.get("isChild"), "isChild"), **state))
            exercise = item.get("workoutExercise")
            if exercise is None:
                continue
            exercise_id = identity(exercise.get("id"), "workout exercise id")
            if exercise_id in seen_performed:
                raise ValueError("Duplicate performed exercise ID")
            seen_performed.add(exercise_id)
            if exercise.get("masterWorkoutExerciseId") is not None and identity(
                exercise["masterWorkoutExerciseId"], "performed parent"
            ) != template_id:
                raise ValueError("Performed exercise parent mismatch")
            check_parent(conn, "performed_exercises", "workout_exercise_id", exercise_id,
                         workout_id, "master_workout_exercise_id", template_id)
            date = text_value(exercise.get("date"), "workout date")
            if date is not None:
                try:
                    datetime.fromisoformat(date)
                except ValueError:
                    raise ValueError("Workout date must be an ISO date/time") from None
            session_key = json.dumps([week, day, date], separators=(",", ":"))
            conn.execute("INSERT INTO workout_sessions(workout_id, session_key, workout_date, week, day) "
                         "VALUES (?,?,?,?,?) ON CONFLICT(workout_id, session_key) DO NOTHING",
                         (workout_id, session_key, date, week, day))
            session_id = conn.execute("SELECT session_id FROM workout_sessions WHERE workout_id=? AND session_key=?",
                                      (workout_id, session_key)).fetchone()[0]
            selected = exercise.get("selectedMasterExerciseId")
            upsert(conn, "performed_exercises", ("workout_id", "workout_exercise_id"), dict(
                workout_id=workout_id, workout_exercise_id=exercise_id,
                master_workout_exercise_id=template_id, session_id=session_id,
                selected_exercise_id=None if selected is None else identity(selected, "selected exercise"),
                completed=boolean(exercise.get("isCompleted"), "exercise isCompleted"),
                note=text_value(exercise.get("note"), "performed note"), **state))
            for set_id, performed in source_map(exercise.get("workoutExerciseSets"), "performed sets"):
                if set_id in seen_sets:
                    raise ValueError("Duplicate performed set ID")
                seen_sets.add(set_id)
                if performed.get("workoutExerciseId") is not None and identity(
                    performed["workoutExerciseId"], "set parent"
                ) != exercise_id:
                    raise ValueError("Performed set parent mismatch")
                check_parent(conn, "performed_sets", "set_id", set_id, workout_id, "workout_exercise_id", exercise_id)
                plan_id = performed.get("masterWorkoutExerciseSetId")
                if plan_id is not None:
                    plan_id = identity(plan_id, "set prescription id")
                    parent = conn.execute("SELECT master_workout_exercise_id FROM prescribed_sets "
                                          "WHERE workout_id=? AND master_workout_exercise_set_id=?",
                                          (workout_id, plan_id)).fetchone()
                    if parent != (template_id,):
                        raise ValueError("Performed set prescription missing or belongs to another exercise")
                reps_raw = performed.get("reps")
                reps = integer(reps_raw, "logged reps")
                raw_weight = performed.get("weight")
                weight = number(raw_weight, "weight")
                kg = lb = None
                if weight is not None:
                    if weight < 0:
                        raise ValueError("Weight cannot be negative")
                    kg = float(weight if weight_unit == "kg" else weight / KG_TO_LB)
                    lb = float(weight * KG_TO_LB if weight_unit == "kg" else weight)
                    if not math.isfinite(kg) or not math.isfinite(lb):
                        raise ValueError("Weight is outside storage range")
                upsert(conn, "performed_sets", ("workout_id", "set_id"), dict(
                    workout_id=workout_id, set_id=set_id, workout_exercise_id=exercise_id,
                    master_workout_exercise_id=template_id, master_workout_exercise_set_id=plan_id,
                    set_order=integer(performed.get("order"), "set order"),
                    logged_reps_raw=None if reps_raw is None else str(reps_raw),
                    actual_reps=reps if reps is not None and reps > 0 else None,
                    weight_raw=None if raw_weight is None else str(raw_weight), source_weight_unit=weight_unit,
                    weight_kg=kg, weight_lb=lb, completed=boolean(performed.get("isCompleted"), "set isCompleted"),
                    is_pr=boolean(performed.get("isPr"), "isPr"), **state))
    return import_id


def run_import(workout_file, master_file, db_file=DEFAULT_DB_FILE, **options):
    db_file = Path(db_file)
    # Protect the production file even if supplied explicitly (including hard links).
    protected = ROOT / "data" / "training.db"
    if db_file.resolve() == protected.resolve() or (
        db_file.exists() and protected.exists() and db_file.samefile(protected)
    ):
        raise ValueError("training.db is protected; choose a separate v2 database")
    workout_file, master_file = Path(workout_file), Path(master_file)
    workout_raw, catalog_raw = workout_file.read_bytes(), master_file.read_bytes()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    try:
        create_schema(conn)
        import_id = import_snapshot(conn, workout_raw, catalog_raw,
                                    workout_path=str(workout_file.resolve()),
                                    catalog_path=str(master_file.resolve()), **options)
        counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("import_snapshots", "exercise_catalog", "workout_instances", "workout_sessions", *STATE_TABLES)}
        return {"import_id": import_id, "database": str(db_file.resolve()), "database_totals": counts}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workout", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_FILE)
    parser.add_argument("--workout-id", type=int)
    parser.add_argument("--master-workout-id", type=int)
    parser.add_argument("--snapshot-mode", choices=("partial", "full"), default="partial",
                        help="full asserts the JSON is a complete snapshot for this workout")
    parser.add_argument("--source-weight-unit", choices=("kg", "lb"), default="kg",
                        help="explicit assumption, not a documented BioLayne contract")
    args = parser.parse_args()
    try:
        result = run_import(args.workout, args.master, args.db, workout_id=args.workout_id,
                            master_workout_id=args.master_workout_id, mode=args.snapshot_mode,
                            weight_unit=args.source_weight_unit)
    except (ValueError, OSError, sqlite3.Error, InvalidOperation) as exc:
        # Do not echo source contents, SQL bindings, or raw malformed values.
        parser.exit(1, f"Import failed ({type(exc).__name__}). Check input structure, IDs, database version and access.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
