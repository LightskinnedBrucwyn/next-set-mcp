# Read-only training API

The default `app:app` exposes only health and six training GET routes. Configure `NEXT_SET_COACH_READ_KEY` and optionally `NEXT_SET_DB`. Training routes require `X-Coach-Read-Key`. Missing configuration returns 503; incorrect credentials return 401.

| Route | Purpose |
| --- | --- |
| /training/context | Compact recent context and next prescription |
| /training/latest | Latest recorded session |
| /training/next | First prescribed week/day after the latest inferred-complete session |
| /training/summary | Retained database totals |
| /training/exercise/{exercise_id}/history | Up to five sessions for a canonical exercise ID |
| /training/exercises/search | Up to ten catalog matches for query; has_more indicates truncation |

History limits are 1–5. Search accepts 1–100 characters and matches partial words in any order. Punctuation-only queries are rejected. Context/latest/next support an optional workout_id for disambiguation.

SQLite uses read-only connections and query_only. Missing reps remain unknown, completion is inferred, substitutions stay explicit, and ambiguous program positions return a status instead of a guess. Summary totals include retained history. No route returns raw snapshots, credentials, or SQL access.

The optional BioLayne app has additional protected sync routes and requires a separate sync key; see its adapter documentation.
