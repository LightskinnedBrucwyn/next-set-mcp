# Next_Set MCP Server

Workout apps record the sets. Next_Set makes that history available to the tools you use to understand them.

Next_Set is a local, read-only MCP server for structured training data. It gives AI assistants and compatible applications access to workout history, prescriptions, exercise progression, and upcoming sessions through a normalized training-data layer. BioLayne is currently supported through an optional, unofficial adapter and is not affiliated with or endorsed by Next_Set.

## What it does

- Exposes six MCP tools for training context, summaries, latest and next workouts, exercise history, and exercise search.
- Keeps prescribed work separate from recorded performance, including substitutions and unknown reps.
- Searches canonical exercise names before history and flags truncated results.
- Keeps training data in local SQLite storage. The MCP bridge cannot sync or change workouts.

Next_Set provides data and unit normalization. It does not autonomously prescribe progression or implement a complete coaching/calculation engine. An AI client can receive private training data when tools are called; local storage does not mean responses stay on your machine.

## Architecture

```text
Optional source adapter â†’ SQLite training schema â†’ read-only API â†’ MCP â†’ compatible client
                          â†‘
                    Synthetic demo
```

`app.py`, `coach_api.py`, `storage.py`, and `mcp/` have no BioLayne adapter imports. The optional integration lives in `adapters/biolayne/`. The current schema retains legacy source-style identifiers; other providers need explicit mapping and are not implemented or claimed as supported.

## Try it with synthetic data

Requires Python 3.12. Node.js is needed only for userscript tests; the optional desktop plugin uses Windows PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r mcp/requirements-mcp.txt -r requirements-test.txt
.\.venv\Scripts\python.exe scripts/create-demo.py --db data/demo.db
$env:NEXT_SET_DB = (Resolve-Path data/demo.db).Path
$env:NEXT_SET_COACH_READ_KEY = [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8787 --workers 1
```

Configure the MCP client with the same read key through its secure environment settings. Launch `mcp/server.py` with the checkout Python interpreter using STDIO. Never paste keys into committed config. See [MCP setup](mcp/README.md) and the optional [Windows plugin](plugins/next-set/README.md).

If another service already uses port 8787, choose a free loopback port and set the client's `NEXT_SET_COACH_API_URL` to match. Do not stop or replace an existing installation just to try the demo.

The demo is invented training data. It requires no BioLayne account or upstream access. It refuses to overwrite an existing database.

## Optional BioLayne integration

BioLayne is the first source adapter, not a requirement for the demo or read-only server. See [adapter setup and boundaries](adapters/biolayne/README.md). The adapter requires separate credentials and an explicitly selected launch command; the default API exposes no sync or token routes.

**Next_Set is unofficial and is not affiliated with or endorsed by BioLayne.** BioLayne names and marks belong to their respective owners. This project does not grant permission to access their service or redistribute their content. Permission for public distribution/use of the live integration remains unresolved. Do not bypass access controls or package paid programs, instructional content, or private exports.

## Development

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
node --test tests/test_userscript.cjs
```

Tests use synthetic fixtures, fake credentials, and temporary databases. Real exports, databases, credentials, logs, and environments are excluded from Git.

## Status

Initial Next_Set release candidate, version 0.1.0, derived from the personal Coach plugin 1.0.1. This checkout uses new names and settings and does not modify that installation. See [migration notes](MIGRATION.md).

Before public release: resolve upstream integration permission, choose an open-source license, verify a fresh-machine installation and dependency versions, and validate the optional userscript and reboot recovery. No open-source license has been selected yet.

## Private release verification

After the declared install above, run `python -m pip check`, the full Python and
JavaScript suites, and `.venv/Scripts/python.exe -B scripts/verify-local.py`.
The verifier uses a temporary synthetic DB, free loopback port and process-only
random key; it performs two API restart cycles and all six direct and packaged
STDIO calls. It needs no saved User credential or local plugin binding. Nothing
is installed into a personal marketplace by this verifier.

`requirements.txt` is the API runtime; `mcp/requirements-mcp.txt` is the MCP
runtime; `adapters/biolayne/requirements.txt` is optional; `requirements-test.txt`
aggregates the verification dependencies. Shared Pydantic is explicit in
`requirements-models.txt`. `requirements-lock.txt` constrains the tested Windows
Python 3.12 resolution without installing optional packages by itself. Upgrade
pins and constraints together only after isolated verification.

See [verification evidence](VERIFICATION.md), [migration](MIGRATION.md), and
[Windows startup / adapter acceptance](OPERATIONS.md). This remains private:
license selection and upstream permission/terms are unresolved release gates.
