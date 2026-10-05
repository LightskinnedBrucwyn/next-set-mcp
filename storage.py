"""Source-independent database location and read-only summary."""
import os
import sqlite3
from pathlib import Path
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_FILE = Path(os.environ.get("NEXT_SET_DB", ROOT / "data" / "training-v2.db"))

def training_summary(db_file=DEFAULT_DB_FILE):
    path = Path(db_file).resolve()
    if not path.is_file():
        raise HTTPException(404, "Training database is not available yet.")
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            if conn.execute("PRAGMA user_version").fetchone() != (2,):
                raise HTTPException(503, "Training database schema is not v2.")
            counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("exercise_catalog", "workout_instances", "workout_sessions",
                                    "performed_exercises", "performed_sets")}
            latest = conn.execute("SELECT MAX(workout_date) FROM workout_sessions").fetchone()[0]
            return {"ok": True, "database_totals": counts, "latest_workout_date": latest}
        finally:
            conn.close()
    except sqlite3.Error:
        raise HTTPException(503, "Training summary is temporarily unavailable.") from None
