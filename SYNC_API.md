# Application sync

`POST /sync/workout/{workout_id}` requires the existing `X-Next-Set-Key` and a usable in-memory BioLayne token. It fetches the workout, validates that its scope agrees with the requested workout and one master program, then fetches that program's catalog. No hard-coded program ID is used. Empty workout responses cannot establish program identity and are rejected.

The existing fetch helper retains timeout, redirect rejection, upstream error sanitization, and token forwarding. An optional raw-response mode returns response-body bytes without JSON reserialization. The sync service writes those bytes to `data/raw/workout-{id}/{UTC timestamp}-{UUID}-workout.json` and the corresponding `-master-exercises.json`. Returned snapshot paths are relative to `data/raw`. HTTP headers are never saved. Known credential values and credential-named JSON fields cause rejection before files are written; response bodies must be workout/catalog data, not authentication documents.

The service calls `sync_v2.run_import` with `partial` and the explicit `kg` assumption, targeting `training-v2.db`. It does not duplicate normalization or SQL import logic. All database import DML retains the importer's transaction boundary.

## Failures and concurrency

| Status | Meaning |
| --- | --- |
| 401 | Missing/wrong sync key, unavailable/expired bearer token, or upstream rejection |
| 409 | Another sync is already running |
| 422 | Invalid workout ID or importer validation failure |
| 502 | Network/upstream failure, malformed JSON/catalog, or inconsistent source scope |
| 504 | Upstream timeout |
| 500 | Raw file write or database import failure |

Both fetches and basic response/scope checks must succeed before snapshot writes and import. A fetch failure never imports. Raw files from a completed fetch remain if later importer validation or database writes fail; they are useful for diagnosis. If snapshot writing fails midway, an incomplete raw file or pair may remain, but no import begins. Existing raw files are never overwritten, including forced filename collisions. Schema initialization can leave an empty schema after an import failure, as documented in `SYNC_V2.md`.

One process-wide nonblocking lock covers both fetches, snapshots, and import. Use **one Uvicorn worker**, consistent with the existing process-local token storage. Requests in that process are rejected with 409 while syncing; SQLite transactions still serialize database writes from other connections. Avoid running a separate importer concurrently during normal operation. No scheduler, automatic token-triggered sync, or distributed lock was added.

## Read-only summary

`GET /training/summary` accepts the existing `X-Next-Set-Key` or the separate `X-Coach-Read-Key` documented in `COACH_API.md`, and does not require a live BioLayne token. It opens SQLite with `mode=ro` and `query_only=ON`, returning retained database totals and the latest stored source date. Totals include retained historical rows and inferred session groupings, not just source-present or completed records. An absent database returns 404; schema/database errors return 503. It never creates a database.

## Deployment and approved live check

Automated tests use temporary databases/directories, synthetic JSON as mocked HTTP response bodies, fake credentials, and a network-call tripwire. Neither real BioLayne access nor production writes are needed for tests.

Load the updated code using your existing Uvicorn launch method with one worker. A restart loses the in-memory token by design; allow the existing browser/Tampermonkey flow to deliver a fresh token. No Tailscale, firewall, Tampermonkey, or credential changes are required.

The following **production sync requires explicit approval before the agent executes it**. It creates two production raw snapshots and imports into `training-v2.db`; it does not modify either v1 database. Run it in PowerShell only after the updated app is loaded and a token is available:

```powershell
if (-not $env:NEXT_SET_SYNC_KEY) { throw 'NEXT_SET_SYNC_KEY is not available in this shell.' }
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8787/sync/workout/100' -Headers @{ 'X-Next-Set-Key' = $env:NEXT_SET_SYNC_KEY }
```

Read-only summary:

```powershell
Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8787/training/summary' -Headers @{ 'X-Next-Set-Key' = $env:NEXT_SET_SYNC_KEY }
```

Do not paste credentials into commands, logs, screenshots, or responses. These commands obtain the key from the environment without printing it.
