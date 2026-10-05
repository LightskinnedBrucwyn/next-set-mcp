# Next_Set MCP Server

Workout apps record the sets. Next_Set makes that history available to the tools you use to understand them.

Next_Set is a local, read-only MCP server for structured training data. It gives compatible AI assistants and third-party applications access to exercise history, recorded performance, prescriptions, and the next programmed session—without repeatedly copying numbers or uploading screenshots.

## What it does

- Exposes six MCP tools for training context, summaries, latest and next workouts, exercise history, and exercise search.
- Keeps prescribed work separate from recorded performance, including substitutions and unknown reps.
- Searches canonical exercise names before history and flags truncated results.
- Keeps training data in local SQLite storage. The MCP bridge cannot sync or change workouts.

Next_Set provides data and unit normalization. It does not autonomously prescribe progression or implement a complete coaching/calculation engine. An AI client can receive private training data when tools are called; local storage does not mean responses stay on your machine.

## Architecture

```text
Optional source adapter → SQLite training schema → read-only API → MCP → compatible client
                          ↑
                    Synthetic demo
```

`app.py`, `coach_api.py`, `storage.py`, and `mcp/` have no BioLayne adapter imports. The optional integration lives in `adapters/biolayne/`. The current schema retains legacy source-style identifiers; other providers need explicit mapping and are not implemented or claimed as supported.

## Try it with synthetic data

Requires Python 3.12. Node.js is needed only for userscript tests; the optional desktop plugin uses Windows PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
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
