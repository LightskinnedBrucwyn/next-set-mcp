"""Next_Set read-only training API. No upstream credentials or sync routes."""
import os
import secrets
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Query, Path
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import coach_api
import storage

app = FastAPI(title="Next_Set Training API")
COACH_READ_KEY = os.environ.get("NEXT_SET_COACH_READ_KEY", "")

def verify_coach_key(x_coach_read_key: Optional[str] = Header(default=None, alias="X-Coach-Read-Key")):
    if not COACH_READ_KEY:
        raise HTTPException(503, "Read-only access is not configured.")
    if not x_coach_read_key or not secrets.compare_digest(x_coach_read_key.encode(), COACH_READ_KEY.encode()):
        raise HTTPException(401, "Unauthorized")

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": e["loc"], "type": e["type"], "msg": e["msg"]} for e in exc.errors()]})

@app.get('/health')
def health():
    return {"ok": True, "mode": "read-only"}

@app.get('/training/summary', dependencies=[Depends(verify_coach_key)])
def training_summary():
    return storage.training_summary(coach_api.DB_FILE)

@app.get('/training/context', dependencies=[Depends(verify_coach_key)])
def training_context(history_sessions: int = Query(3, ge=1, le=5),
                     workout_id: Optional[int] = Query(None, ge=1, le=2**63-1)):
    with coach_api.database() as conn:
        return coach_api.context(conn, history_sessions, workout_id)


@app.get('/training/latest', dependencies=[Depends(verify_coach_key)])
def training_latest(workout_id: Optional[int] = Query(None, ge=1, le=2**63-1)):
    with coach_api.database() as conn:
        return coach_api.latest(conn, workout_id)


@app.get('/training/next', dependencies=[Depends(verify_coach_key)])
def training_next(workout_id: Optional[int] = Query(None, ge=1, le=2**63-1)):
    with coach_api.database() as conn:
        return coach_api.next_session(conn, workout_id)


@app.get('/training/exercise/{exercise_id}/history', dependencies=[Depends(verify_coach_key)])
def training_exercise_history(exercise_id: int = Path(..., ge=1, le=2**63-1),
                              history_sessions: int = Query(3, ge=1, le=5)):
    with coach_api.database() as conn:
        return coach_api.history(conn, exercise_id, history_sessions)


@app.get('/training/exercises/search', dependencies=[Depends(verify_coach_key)])
def search_training_exercises(query: str = Query(..., min_length=1, max_length=100)):
    with coach_api.database() as conn:
        return coach_api.search_exercises(conn, query)
