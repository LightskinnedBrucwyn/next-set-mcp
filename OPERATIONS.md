# Personal production plan (not executed)

Batcave Coach remains the known-good runtime. No task, credential, database,
plugin binding, firewall, or Tailscale setting is changed by this plan.

## Windows startup design

Use one Windows Task Scheduler task for the read-only API. Use an **At startup**
trigger with a 30-second delay under the same explicitly selected Windows account
that owns the Next_Set User environment and database. Select **Run whether user
is logged on or not**, without highest privileges. Enter any required Windows
account password only in Task Scheduler's secure UI, never a script or command.
Confirm this account can load its User profile/HKCU at boot. If organizational
policy prevents that, resolve the account setup before enabling unattended boot.
An at-logon-only task does not satisfy reboot-without-login acceptance.

The task action should invoke a reviewed PowerShell wrapper with `-NoProfile`
and an absolute wrapper path. That future wrapper must derive the checkout from
its own location, read only `NEXT_SET_COACH_READ_KEY` from Windows User scope,
validate a nonempty key and an existing explicit database path, and start the
checkout's Python with `-B -m uvicorn app:app --host 127.0.0.1 --port <chosen-port>
--workers 1`. Forward only an OS environment allowlist plus the read key and
`NEXT_SET_DB` to that child; never read/forward either sync key. Fail closed on
missing configuration, a missing database, or a port already in use. Do not kill
an unknown listener. Keep the wrapper waiting for its owned child and returning
its exit status. Wire graceful stop/child cleanup before deployment; do not use
an untracked detached child that survives a task stop.

Use a selected free port during migration (for example 8788, only after checking
availability). Logs belong in ignored private local storage with account-only
access, timestamps and rotation (for example seven daily files, 10 MB each).
Log startup/shutdown and sanitized status codes; disable access-log query strings
and never log headers, credentials, upstream bodies, or workout payloads.

Task settings: do not start a second instance; restart after failure with a
one-minute delay and a bounded retry count; no daily execution-time cutoff.
Stop and restart only this task/owned process after checking port ownership.
Validate a real reboot, authenticated read, missing-key failure, occupied-port
failure, repeated restart, log rotation, and complete process cleanup before
calling startup production-ready. No scheduled task or startup wrapper has been
installed yet. A separate adapter startup plan is needed only if adapter hosting
moves; the default API intentionally has no sync routes.

## Optional browser/source adapter acceptance (pending)

Use an authorized private account and staging database; no production writes
until approval. Record timestamps, HTTP status and row counts, never secret values
or source payloads. Exercise this matrix with exactly one userscript enabled:

| Scenario | Required observation |
| --- | --- |
| Initial token acquisition | Existing signed-in browser session delivers a valid token; no cookie/password extraction or token logging |
| Refresh / expired token | Expired token is rejected; an authorized browser refresh obtains a replacement; no endless retry loop |
| Completed workout | Debounced sync imports the intended workout only; expected session/set completion and pounds verified |
| Repeated sync | Snapshot count may increase; performed row IDs/counts do not duplicate |
| Read/sync auth mismatch | Wrong, missing and exchanged keys rejected; coach key cannot write |
| Upstream timeout or malformed data | Sanitized actionable failure; transaction rolls back; prior training rows remain intact |
| Logged-out browser | Quiet bounded failure, no credential prompts or bypass |
| Substitution / unknown reps | Selected exercise resolves correctly; raw zero reps stay unknown; targets remain separate |

The 13 automated userscript tests cover synthetic behavior, not these live
provider or browser acceptance checks. Permission/terms review remains required;
no live adapter testing or upstream requests were performed for this release work.
