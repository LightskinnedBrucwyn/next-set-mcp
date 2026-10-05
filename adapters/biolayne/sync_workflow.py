"""Application sync orchestration; imports remain owned by sync_v2."""
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import DecimalException
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from adapters.biolayne import sync_v2

DB_FILE = sync_v2.DEFAULT_DB_FILE
RAW_DIR = sync_v2.ROOT / "data" / "raw"
SYNC_LOCK = threading.Lock()


def validate_body(raw, sensitive_values):
    """Reject credential-bearing responses instead of persisting or redacting them."""
    value = sync_v2.parse_json(raw)
    forbidden = {"authorization", "cookie", "cookies", "setcookie", "nonce",
                 "wpnonce", "xwpnonce", "token", "accesstoken", "refreshtoken",
                 "bearertoken", "xbatcavekey", "batcavesynckey", "nextsetsynckey", "nextsetcoachreadkey", "xnextsetkey", "password", "secret"}

    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = ''.join(c for c in key.lower() if c.isalnum())
                if normalized in forbidden:
                    raise ValueError("Credential field in source")
                inspect(key)
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
        elif isinstance(item, str):
            if any(secret and secret in item for secret in sensitive_values):
                raise ValueError("Credential value in source")
    inspect(value)
    return value


def synchronize(workout_id, fetch, *, sensitive_values=()):
    try:
        sync_v2.identity(workout_id, "workout_id")
    except ValueError:
        raise HTTPException(422, "Workout ID must be a positive SQLite integer.") from None
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, "A workout sync is already running. Try again after it completes.")
    try:
        workout_raw = fetch("workout-exercises", {"workoutId": workout_id}, raw=True)
        try:
            rows = validate_body(workout_raw, sensitive_values)
            _, master_id = sync_v2.scope(rows, workout_id, None)
        except (ValueError, TypeError, DecimalException, RecursionError):
            raise HTTPException(502, "Invalid workout response: check JSON structure and consistent workout/program IDs.") from None

        catalog_raw = fetch("master-exercises", {"masterWorkoutId": master_id}, raw=True)
        try:
            sync_v2.object_value(validate_body(catalog_raw, sensitive_values), "catalog")
        except (ValueError, TypeError, DecimalException, RecursionError):
            raise HTTPException(502, "Invalid exercise catalog response.") from None

        # Exclusive creation: even a timestamp/UUID collision cannot overwrite a snapshot.
        folder = Path(RAW_DIR) / f"workout-{workout_id}"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex
        workout_file = folder / f"{stamp}-workout.json"
        master_file = folder / f"{stamp}-master-exercises.json"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with workout_file.open("xb") as output:
                output.write(workout_raw)
            with master_file.open("xb") as output:
                output.write(catalog_raw)
        except OSError:
            raise HTTPException(500, "Unable to save raw snapshots. No database import was attempted.") from None

        try:
            result = sync_v2.run_import(workout_file, master_file, DB_FILE,
                                        workout_id=workout_id, master_workout_id=master_id,
                                        mode="partial", weight_unit="kg")
        except (ValueError, TypeError, DecimalException, OverflowError, RecursionError):
            raise HTTPException(422, "Source data failed importer validation. Raw snapshots were retained.") from None
        except (sqlite3.Error, OSError):
            raise HTTPException(500, "Database import failed. Raw snapshots were retained; inspect local database access and schema.") from None
        return {"ok": True, "workout_id": workout_id, "master_workout_id": master_id,
                "import_id": result["import_id"], "database_totals": result["database_totals"],
                "snapshot": {"workout_file": workout_file.relative_to(RAW_DIR).as_posix(),
                             "master_file": master_file.relative_to(RAW_DIR).as_posix()}}
    finally:
        SYNC_LOCK.release()


def training_summary():
    from storage import training_summary as read_summary
    return read_summary(DB_FILE)
