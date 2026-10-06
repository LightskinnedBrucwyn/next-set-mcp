# Private production-candidate verification

Verified on Windows on 2026-10-06 against baseline
`48eb0fe48fb8b3c78957b3fe552ae6bf0a49d13a` plus the reviewed working-tree changes.
No Batcave runtime, production database, stored credential, browser, plugin
installation, scheduled task, firewall or Tailscale configuration was changed.

## Isolated environment

Cloned origin into a newly generated Windows temp directory, overlaid only the
reviewed source candidate, and created a brand-new venv using the system Python.
The venv configuration has no Batcave path. No preexisting project environment,
private exports, saved User key, or LocalAppData binding was used by verification.
The verifier creates a temporary synthetic DB, random process-only coach key,
and free localhost listener, and passes explicit ProjectRoot/ApiUrl/KeySource to
the existing packaged launcher. It does not register or install a plugin.

Install command (from the fresh checkout):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r mcp/requirements-mcp.txt -r requirements-test.txt
```

The baseline API-only install was insufficient for verification; MCP and test
requirements are intentional dependencies of that workflow. All direct external
imports are now checked against declared dependency closures in regression tests.

## Results

- `pip check`: PASS.
- Python: 99 tests PASS (including dependency separation/import declarations and
  explicit-root failure without falling back to a personal binding).
- JavaScript: 13 tests PASS.
- Portable plugin and MCP manifest schemas: PASS.
- Two API start/stop cycles: PASS; server threads joined and listeners closed.
- Missing/wrong read key rejected; default API `/token` route absent.
- Synthetic database byte comparison before/after calls: unchanged.
- All six direct STDIO and packaged PowerShell STDIO tools passed in each cycle:
  `get_training_summary`, `get_training_context`, `get_latest_workout`,
  `get_next_workout`, `get_exercise_history`, `search_exercises`.
- Package smoke checks tool discovery, read-only annotations, output schemas,
  strict JSON and equality of text/structured results.
- Candidate scan: no current credential values, token/private-key patterns, or
  tracked production databases found. Test data is synthetic.

## Recorded versions

| Component | Version |
| --- | --- |
| Python | 3.12.10 |
| pip | 25.0.1 |
| Node | 24.17.0 |
| FastAPI | 0.141.1 |
| Uvicorn | 0.53.0 |
| Pydantic | 2.13.5 |
| MCP SDK | 2.3.0 |
| httpx | 0.28.1 |
| jsonschema | 4.26.0 |
| requests (optional adapter) | 2.34.2 |

Complete resolved package constraints are in `requirements-lock.txt` (Windows,
Python 3.12). Other OS/Python combinations are not certified by this run.
CI creates the same venv layout and runs the suites plus isolated verification.
A post-push GitHub CI result is separate from these local CI-equivalent results.

## Remaining gates

Migration and boot recovery are **planned, not executed**; see MIGRATION.md and
OPERATIONS.md. Live provider/browser acceptance and real Desktop migration remain
pending. Public release requires a license decision and BioLayne permission/terms
review. Repository visibility must be verified private before the requested push;
no visibility change or public publication is authorized by these checks.
