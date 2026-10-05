CREATE TABLE schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL CHECK (version = 2)
);
INSERT INTO schema_metadata VALUES (1, 2);
PRAGMA user_version = 2;

CREATE TABLE programs (master_workout_id INTEGER PRIMARY KEY CHECK(master_workout_id > 0));
CREATE TABLE workout_instances (
    workout_id INTEGER PRIMARY KEY CHECK(workout_id > 0),
    master_workout_id INTEGER NOT NULL REFERENCES programs,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE import_snapshots (
    import_id INTEGER PRIMARY KEY,
    imported_at TEXT NOT NULL,
    workout_id INTEGER NOT NULL REFERENCES workout_instances,
    master_workout_id INTEGER NOT NULL REFERENCES programs,
    workout_path TEXT NOT NULL,
    catalog_path TEXT NOT NULL,
    snapshot_mode TEXT NOT NULL CHECK(snapshot_mode IN ('partial', 'full')),
    assumed_weight_unit TEXT NOT NULL CHECK(assumed_weight_unit IN ('kg', 'lb')),
    workout_sha256 TEXT NOT NULL,
    catalog_sha256 TEXT NOT NULL,
    workout_json BLOB NOT NULL,
    catalog_json BLOB NOT NULL
);
CREATE TABLE exercise_catalog (
    exercise_id INTEGER PRIMARY KEY CHECK(exercise_id > 0),
    name TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_import_id INTEGER NOT NULL REFERENCES import_snapshots
);
CREATE TABLE prescribed_exercises (
    workout_id INTEGER NOT NULL REFERENCES workout_instances,
    master_workout_exercise_id INTEGER NOT NULL CHECK(master_workout_exercise_id > 0),
    prescribed_exercise_id INTEGER NOT NULL CHECK(prescribed_exercise_id > 0),
    week INTEGER, day INTEGER, display_order INTEGER,
    prescribed_sets INTEGER, prescribed_reps INTEGER, rep_range TEXT,
    prescribed_rir TEXT, intensity_technique TEXT, note TEXT, tempo TEXT, rest TEXT,
    source_present INTEGER NOT NULL CHECK(source_present IN (0,1)),
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    last_import_id INTEGER NOT NULL REFERENCES import_snapshots,
    PRIMARY KEY(workout_id, master_workout_exercise_id)
);
CREATE TABLE prescribed_sets (
    workout_id INTEGER NOT NULL,
    master_workout_exercise_set_id INTEGER NOT NULL CHECK(master_workout_exercise_set_id > 0),
    master_workout_exercise_id INTEGER NOT NULL,
    set_order INTEGER, target_reps INTEGER, target_rir TEXT, note TEXT,
    is_to_failure INTEGER CHECK(is_to_failure IN (0,1)),
    is_child INTEGER CHECK(is_child IN (0,1)),
    source_present INTEGER NOT NULL CHECK(source_present IN (0,1)),
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    last_import_id INTEGER NOT NULL REFERENCES import_snapshots,
    PRIMARY KEY(workout_id, master_workout_exercise_set_id),
    UNIQUE(workout_id, master_workout_exercise_set_id, master_workout_exercise_id),
    FOREIGN KEY(workout_id, master_workout_exercise_id)
        REFERENCES prescribed_exercises(workout_id, master_workout_exercise_id)
);
-- A workoutId can span multiple dates. Session identity is an inferred grouping,
-- not a claimed upstream session ID. Empty grouping parts mean unknown.
CREATE TABLE workout_sessions (
    session_id INTEGER PRIMARY KEY,
    workout_id INTEGER NOT NULL REFERENCES workout_instances,
    session_key TEXT NOT NULL,
    workout_date TEXT, week INTEGER, day INTEGER,
    UNIQUE(workout_id, session_key),
    UNIQUE(workout_id, session_id)
);
CREATE TABLE performed_exercises (
    workout_id INTEGER NOT NULL,
    workout_exercise_id INTEGER NOT NULL CHECK(workout_exercise_id > 0),
    master_workout_exercise_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    selected_exercise_id INTEGER CHECK(selected_exercise_id > 0),
    completed INTEGER CHECK(completed IN (0,1)),
    note TEXT,
    source_present INTEGER NOT NULL CHECK(source_present IN (0,1)),
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    last_import_id INTEGER NOT NULL REFERENCES import_snapshots,
    PRIMARY KEY(workout_id, workout_exercise_id),
    UNIQUE(workout_id, workout_exercise_id, master_workout_exercise_id),
    FOREIGN KEY(workout_id, master_workout_exercise_id)
        REFERENCES prescribed_exercises(workout_id, master_workout_exercise_id),
    FOREIGN KEY(workout_id, session_id) REFERENCES workout_sessions(workout_id, session_id)
);
CREATE TABLE performed_sets (
    workout_id INTEGER NOT NULL,
    set_id INTEGER NOT NULL CHECK(set_id > 0),
    workout_exercise_id INTEGER NOT NULL,
    master_workout_exercise_id INTEGER NOT NULL,
    master_workout_exercise_set_id INTEGER,
    set_order INTEGER,
    logged_reps_raw TEXT, actual_reps INTEGER,
    weight_raw TEXT, source_weight_unit TEXT NOT NULL CHECK(source_weight_unit IN ('kg','lb')),
    weight_kg REAL, weight_lb REAL,
    completed INTEGER CHECK(completed IN (0,1)),
    is_pr INTEGER CHECK(is_pr IN (0,1)),
    source_present INTEGER NOT NULL CHECK(source_present IN (0,1)),
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    last_import_id INTEGER NOT NULL REFERENCES import_snapshots,
    PRIMARY KEY(workout_id, set_id),
    FOREIGN KEY(workout_id, workout_exercise_id, master_workout_exercise_id)
        REFERENCES performed_exercises(workout_id, workout_exercise_id, master_workout_exercise_id),
    FOREIGN KEY(workout_id, master_workout_exercise_set_id, master_workout_exercise_id)
        REFERENCES prescribed_sets(workout_id, master_workout_exercise_set_id, master_workout_exercise_id)
);
CREATE INDEX prescribed_exercises_schedule ON prescribed_exercises(workout_id, week, day, display_order);
CREATE INDEX performed_sets_exercise ON performed_sets(workout_id, workout_exercise_id, set_order);
CREATE INDEX performed_exercises_session ON performed_exercises(session_id);
