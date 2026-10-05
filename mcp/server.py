import os
import sys
from urllib.parse import urlsplit
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, Field
from mcp.types import ToolAnnotations

try:
    from mcp.server import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

SERVER_NAME = "next-set"
DEFAULT_API_URL = "http://127.0.0.1:8787"

INSTRUCTIONS = (
    "Read-only private training tools. Prefer pounds for user-facing weights. "
    "actual_reps=null means unknown; raw reps=0 must never be treated as zero performed reps. "
    "Keep prescription separate from performance and never fabricate missing workout data. "
    "Use get_training_context first for general workout questions and exercise history for progression."
)

try:
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
except TypeError:
    server = MCPServer(SERVER_NAME)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
)

def _api_url() -> str:
    url = os.environ.get("NEXT_SET_COACH_API_URL", DEFAULT_API_URL).rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise RuntimeError('Coach API URL must be a local HTTP origin without credentials or a path.')
    return url

def _coach_key() -> str:
    if os.environ.get('NEXT_SET_COACH_KEY_SOURCE') == 'windows-user':
        # Inspector's env config contains only this non-secret selector. Never put
        # the actual credential in -e arguments or Inspector's serializable config.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_READ) as user_env:
                key, _ = winreg.QueryValueEx(user_env, 'NEXT_SET_COACH_READ_KEY')
        except (ImportError, OSError):
            key = ''
    else:
        key = os.environ.get("NEXT_SET_COACH_READ_KEY", "")
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError(
            "NEXT_SET_COACH_READ_KEY is not available to the MCP process. "
            "Configure the Windows User coach key and use mcp/start-inspector.ps1."
        )
    if not key.isascii() or '\r' in key or '\n' in key:
        raise RuntimeError('Coach key cannot be represented safely as an HTTP header.')
    return key

def _bounded_int(value: int, *, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value

async def _get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"X-Coach-Read-Key": _coach_key()}
    try:
        async with httpx.AsyncClient(
            base_url=_api_url(),
            headers=headers,
            timeout=httpx.Timeout(10.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.get(path, params=params)
    except (httpx.RequestError, ValueError):
        raise RuntimeError(
            "Next_Set API is unavailable on localhost. "
            "Confirm Uvicorn is running on 127.0.0.1:8787."
        ) from None

    if response.status_code == 401:
        raise RuntimeError("Next_Set API rejected the read-only coach credential.")
    if response.status_code == 404:
        raise RuntimeError("Requested training data was not found.")
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Next_Set API returned HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("Next_Set API returned invalid JSON.") from None

    if not isinstance(payload, dict):
        raise RuntimeError("Next_Set API returned an unexpected response shape.")
    return payload

@server.tool(title="Get training context", annotations=READ_ONLY)
async def get_training_context(
    history_sessions: Annotated[int, Field(ge=1, le=5, description="Number of recent sessions per exercise, from 1 to 5.")] = 3,
) -> dict[str, Any]:
    """Get compact coaching context for general workout questions. Read-only."""
    history_sessions = _bounded_int(history_sessions, minimum=1, maximum=5, name="history_sessions")
    return await _get_json("/training/context", params={"history_sessions": history_sessions})

@server.tool(title="Get latest workout", annotations=READ_ONLY)
async def get_latest_workout() -> dict[str, Any]:
    """Get the latest recorded workout session with performed exercises and sets. Read-only."""
    return await _get_json("/training/latest")

@server.tool(title="Get next workout", annotations=READ_ONLY)
async def get_next_workout() -> dict[str, Any]:
    """Get the next prescribed workout. Does not invent progression weights. Read-only."""
    return await _get_json("/training/next")

@server.tool(title="Get exercise history", annotations=READ_ONLY)
async def get_exercise_history(
    exercise_id: Annotated[int, Field(ge=1, le=2**63-1, description="Numeric exercise ID from returned training data, not a workout or performed-row ID.")],
    history_sessions: Annotated[int, Field(ge=1, le=5, description="Number of recent sessions, from 1 to 5.")] = 5,
) -> dict[str, Any]:
    """Get performed history for one exercise, including substitutions. Read-only."""
    exercise_id = _bounded_int(exercise_id, minimum=1, maximum=2**63-1, name="exercise_id")
    history_sessions = _bounded_int(history_sessions, minimum=1, maximum=5, name="history_sessions")
    return await _get_json(
        f"/training/exercise/{exercise_id}/history",
        params={"history_sessions": history_sessions},
    )

@server.tool(title="Get training summary", annotations=READ_ONLY)
async def get_training_summary() -> dict[str, Any]:
    """Get database totals and latest workout date. Read-only."""
    return await _get_json("/training/summary")


class ExerciseMatch(BaseModel):
    exercise_id: int
    name: str


class ExerciseSearchResult(BaseModel):
    matches: list[ExerciseMatch] = Field(max_length=10)
    has_more: bool


@server.tool(title="Search exercises", annotations=READ_ONLY)
async def search_exercises(
    query: Annotated[str, Field(min_length=1, max_length=100,
        description="Exercise name or partial words, such as bench press. Case-insensitive; all words must match.")],
) -> ExerciseSearchResult:
    """Find up to ten canonical catalog exercise names/IDs before requesting history. Ask the user if matches are ambiguous."""
    return ExerciseSearchResult.model_validate(
        await _get_json('/training/exercises/search', params={'query': query}))

def main() -> None:
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport != "stdio":
        raise SystemExit("Only stdio transport is supported by this local bridge.")
    server.run(transport=transport)

if __name__ == "__main__":
    main()
