import os
import secrets
import time
from typing import Optional

import requests
from adapters.biolayne import sync_workflow
import coach_api
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Path
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

API_BASE = "https://app-wobuilder-prod-001.azurewebsites.net/api"

# DO NOT paste your actual key here.
# Windows supplies it through the NEXT_SET_SYNC_KEY environment variable.
SYNC_KEY: str = os.environ.get("NEXT_SET_SYNC_KEY", "")

if not SYNC_KEY:
    raise RuntimeError(
        "NEXT_SET_SYNC_KEY is not set. Refusing to start without API protection."
    )

app = FastAPI(title="BioLayne Sync")
COACH_READ_KEY = os.environ.get("NEXT_SET_COACH_READ_KEY", "")


def verify_coach_key(x_coach_read_key: Optional[str] = Header(default=None, alias="X-Coach-Read-Key")):
    if not COACH_READ_KEY or secrets.compare_digest(COACH_READ_KEY.encode(), SYNC_KEY.encode()):
        raise HTTPException(503, "Coach access is not configured with a separate read key.")
    if not x_coach_read_key or not secrets.compare_digest(x_coach_read_key.encode(), COACH_READ_KEY.encode()):
        raise HTTPException(401, "Unauthorized")


def verify_summary_key(
    x_next_set_key: Optional[str] = Header(default=None, alias="X-Next-Set-Key"),
    x_coach_read_key: Optional[str] = Header(default=None, alias="X-Coach-Read-Key"),
):
    # Backward-compatible operational summary; the other coach routes are coach-only.
    if x_coach_read_key is not None:
        return verify_coach_key(x_coach_read_key)
    return verify_sync_key(x_next_set_key)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    # Validation errors may contain submitted credentials in input/context.
    return JSONResponse(
        status_code=422,
        content={"detail": [
            {"loc": error["loc"], "type": error["type"], "msg": error["msg"]}
            for error in exc.errors()
        ]},
    )

_token: Optional[str] = None
_expires: int = 0


class TokenPayload(BaseModel):
    token: str
    expires: int


def verify_sync_key(
    x_next_set_key: Optional[str] = Header(
        default=None,
        alias="X-Next-Set-Key",
    )
):
    if not x_next_set_key:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    if not secrets.compare_digest(
        x_next_set_key.encode("utf-8"), SYNC_KEY.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )


@app.get("/health")
def health():
    return {
        "ok": True,
        "has_token": bool(_token),
        "token_expires": _expires or None,
        "seconds_remaining": (
            max(0, _expires - int(time.time()))
            if _expires
            else None
        ),
    }


@app.post(
    "/token",
    dependencies=[Depends(verify_sync_key)]
)
def receive_token(payload: TokenPayload):
    global _token, _expires

    _token = payload.token
    _expires = payload.expires

    return {
        "ok": True,
        "expires": _expires,
    }


def auth_headers():
    if not _token:
        raise HTTPException(
            status_code=401,
            detail="No BioLayne token has been received yet.",
        )

    if _expires <= int(time.time()) + 30:
        raise HTTPException(
            status_code=401,
            detail="BioLayne token is expired or about to expire.",
        )

    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {_token}",
        "Origin": "https://biolayne.com",
        "Referer": "https://biolayne.com/",
    }


def fetch_biolayne(path: str, params: dict[str, int], *, raw=False):
    headers = auth_headers()
    try:
        response = requests.get(
            f"{API_BASE}/{path}",
            params=params,
            headers=headers,
            timeout=30,
            # A redirect must not carry credentials to another destination.
            allow_redirects=False,
        )
        try:
            if response.status_code == 401:
                raise HTTPException(
                    status_code=401,
                    detail="BioLayne rejected the bearer token.",
                )
            if not 200 <= response.status_code < 300:
                raise HTTPException(
                    status_code=502,
                    detail="BioLayne returned an unsuccessful response.",
                )
            try:
                if raw:
                    return response.content
                return response.json()
            except ValueError:
                raise HTTPException(
                    status_code=502,
                    detail="BioLayne returned an invalid JSON response.",
                ) from None
        finally:
            response.close()
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="BioLayne request timed out.") from None
    except requests.ConnectionError:
        raise HTTPException(status_code=502, detail="Unable to connect to BioLayne.") from None
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="BioLayne request failed.") from None


@app.get(
    "/workout/{workout_id}",
    dependencies=[Depends(verify_sync_key)],
)
def get_workout(workout_id: int):
    return fetch_biolayne("workout-exercises", {"workoutId": workout_id})


@app.get(
    "/master-exercises/{master_workout_id}",
    dependencies=[Depends(verify_sync_key)],
)
def get_master_exercises(master_workout_id: int):
    return fetch_biolayne(
        "master-exercises", {"masterWorkoutId": master_workout_id}
    )


@app.post("/sync/workout/{workout_id}", dependencies=[Depends(verify_sync_key)])
def sync_workout(workout_id: int):
    # Keep the existing token validation and forwarding unchanged.
    auth_headers()
    return sync_workflow.synchronize(
        workout_id, fetch_biolayne, sensitive_values=(SYNC_KEY, _token)
    )


@app.get("/training/summary", dependencies=[Depends(verify_summary_key)])
def training_summary():
    return sync_workflow.training_summary()


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
