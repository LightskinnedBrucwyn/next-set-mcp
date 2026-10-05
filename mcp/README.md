# Next_Set MCP

Run the read-only API as described in the root README, then launch `mcp/server.py` with your Python interpreter over STDIO. Use an absolute script path in your client. The bridge only calls loopback HTTP GET endpoints, disables environment proxies, and refuses redirects.

Configure `NEXT_SET_COACH_READ_KEY` using the client's secure environment mechanism. `NEXT_SET_COACH_API_URL` defaults to http://127.0.0.1:8787. On Windows, `NEXT_SET_COACH_KEY_SOURCE=windows-user` instead reads the read key from the current user's environment registry entry. Never place credential values in committed manifests or command arguments.

Six tools: get_training_context, get_training_summary, get_latest_workout, get_next_workout, get_exercise_history, search_exercises. The search tool has validated input/output schemas. Responses can contain private training data and are delivered to the calling client.

`smoke_test.py` exercises all six tools against an already-running API and may print training summaries. Keep that output private. Automated tests exercise real STDIO against a synthetic local fixture. The optional Windows plugin launcher is documented in ../plugins/next-set/README.md.

`start-inspector.ps1` uses an already-installed Inspector and reads the Windows User key. Its Inspector 2.9.0 behavior was checked in the original environment; a fresh installation still needs verification. It does not install software automatically.
