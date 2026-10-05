# BioLayne SQLite v2

The importer is offline and uses Python's standard library. `sync.py` is the CLI entry point, `sync_v2.py` implements ingestion, and `schema_v2.sql` defines schema version 2. The existing FastAPI app and authentication remain unchanged.

## Schema

| Table | Identity and purpose |
| --- | --- |
| schema_metadata | Version 2, checked together with SQLite `user_version` |
| programs | Source `masterWorkoutId` |
| workout_instances | Source `workoutId`, linked to its master program |
| import_snapshots | One record per successful ingestion, including exact source bytes, SHA-256 hashes, source paths, scope, time, mode, and unit assumption |
| exercise_catalog | Source exercise ID and latest name |
| prescribed_exercises | `(workout_id, master_workout_exercise_id)`; template prescription as observed for this workout instance |
| prescribed_sets | `(workout_id, master_workout_exercise_set_id)`; retained even with no performed sets |
| workout_sessions | Inferred grouping within a workout by `(week, day, source date/time)` |
| performed_exercises | `(workout_id, workout_exercise_id)`; selected exercise, session, completion, note, and prescription relationship |
| performed_sets | `(workout_id, set_id)`; actual/raw reps, raw/normalized weight, completion, PR, order, and parent relationships |

Prescription copies are scoped to workout instances intentionally: different instances may reuse source template IDs or observe different template versions. Composite foreign keys prevent cross-workout and cross-exercise relationships. Catalog IDs in prescriptions/selections need not exist in a partial catalog; consumers should show an unknown-name fallback rather than silently relabel a selected exercise.

`workoutId` is not treated as a single calendar session: one workout can contain multiple source dates. Session identity is inferred, not an upstream guarantee. Null dates remain unknown, times/timezones are not invented, and source date strings are preserved. If a source date is corrected, the exercise points to a new grouping; prior groupings can remain without current exercises. For current sessions, join to performed exercises with `source_present=1` instead of counting every retained grouping.

## History and import semantics

- Reimporting updates current entity rows without duplicating them. Each successful ingestion still creates its own snapshot record as requested.
- Exact input bytes preserve prior entity values and numeric spellings, including details not promoted into normalized columns. Current tables are the latest representation; previous values are recoverable from snapshots, not a built-in temporal SQL view.
- `partial` is the safe default. Missing entities are untouched. Each included entity is treated as its complete current representation, not a field-level patch; omitted optional scalar fields become NULL.
- `full` is an explicit assertion that the file is a complete snapshot for exactly one workout. It marks that workout's existing prescription/performance rows absent before restoring rows seen in the import. It never marks another workout's records absent, even if both share a program.
- Empty full snapshots require both explicit scope IDs. Unstarted workouts with no nested workout IDs require `--workout-id`; program identity can still be inferred from prescription rows.
- Soft state uses `source_present`, `first_seen_at`, `last_seen_at`, and `last_import_id`. The latter means last observed import, not the import that marked an entity absent. Snapshot records retain the reconciliation history.
- Prescribed set links must exist in the current import or previously stored data and belong to the same exercise. An unresolved reference fails closed; a null source link remains null.
- The entire snapshot record, catalog changes, instance/session changes, and source-state mutations share one transaction. Validation failures roll back those changes. Initial schema creation and an empty database file may remain after failure.
- Snapshots accumulate by design. No retention/deletion job runs.

## Numeric interpretation

User-facing weight preference: pounds (lb). Future UI and analysis outputs should use `weight_lb`, labeled lb and rounded to at most two decimal places for display. Keep stored conversion precision and the original kg/raw values. Do not round stored weights to plate increments or use display rounding for calculations.

Zero logged reps stay raw `0`, with interpreted actual reps NULL. Positive integral reps are retained; fractions, negative reps, invalid IDs, invalid booleans, non-finite numbers, ambiguous duplicate IDs/JSON keys, and practical parent mismatches fail validation. Missing booleans remain NULL rather than being asserted false.

Kilograms are the default explicit assumption supported by sample values, not a documented upstream contract. `--source-weight-unit lb` is also supported, and the assumption is recorded on every import and performed set. Zero weight remains numeric zero. Raw numeric text is stored separately from floating-point query columns; exact original JSON bytes remain in snapshots. The illustrative value `61.23492` converts to approximately `134.9998898791` pounds using the chosen conversion factor; it is not silently rounded to 135.

## Migration and production protection

Default output is `data/training-v2.db`. This implementation refuses `data/training.db`, including aliases detected through file identity, and refuses any nonempty unversioned or differently versioned database. There is no automatic v1 migration. `CREATE TABLE IF NOT EXISTS` is not used as a migration strategy.

Reimporting the current JSON into v2 does not transfer history existing only in v1. Keep `training.db` and its backup. Any future v1 history migration or production switch requires a separate, explicit decision.

## Commands

From `C:\path\to\biolayne-sync`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
python .\sync.py --workout .\data\workout.json --master .\data\catalog.json --db .\data\training-v2.db --snapshot-mode partial --source-weight-unit kg
```

If a future file is confirmed complete, use `--snapshot-mode full`. For an unstarted workout, add `--workout-id <source-id>`. For an empty snapshot, also add `--master-workout-id <source-id>`. An instance ID is never invented from the filename.

## Validation

The suite exercises isolated temporary SQLite files, including two imports of synthetic fixtures, integrity and foreign-key checks, separate instances with overlapping source IDs, substitution, raw numeric precision, zero/missing values, full/partial reconciliation, snapshot retention, late-failure rollback, schema refusal, and the CLI from a different working directory. Existing API tests use mocked network calls and fake credentials.

The apparent Python failure was a sandbox access limitation: the installed Python and existing virtual environment both run outside that sandbox. Neither was rebuilt.
