# Moving to Next_Set

This is a separate release checkout. The original installation remains unchanged.

- Plugin and MCP server ID: `next-set`; display name: Next_Set.
- Settings: `NEXT_SET_COACH_READ_KEY`, `NEXT_SET_COACH_API_URL`, `NEXT_SET_COACH_KEY_SOURCE`, and optional adapter `NEXT_SET_SYNC_KEY`.
- Database selection: `NEXT_SET_DB`; default is this checkout's ignored `data/training-v2.db`.
- Plugin binding: Windows LocalAppData `NextSet/project.json`, created only when its preparation script is run.
- Browser storage and sync header names changed. Configure the renamed userscript separately; never run duplicate sync scripts.

There is no automatic credential migration, plugin installation, database copy, server restart, or network change. First validate with the synthetic demo. Deliberately configure private storage and credentials before changing an existing installation. Do not point both sync services at the same database.

## Staged migration checklist (do not execute automatically)

1. Record the current Batcave checkout/version, API launch method, private data
   location, plugin version/binding and listener ownership. Keep its plugin and
   launcher installed and available throughout staging.
2. Make a private SQLite **online backup** using `sqlite3.Connection.backup` from
   a read-only source connection into a new protected destination. Do not copy
   only the main file while WAL writes are possible. Keep raw snapshots privately
   if needed for rollback; never add backups or exports to Git. Verify
   `integrity_check`, `user_version=2`, counts and sample semantics on the backup.
3. Next_Set retains schema v2 and source IDs, but compatibility with this user's
   live DB has not been tested in this work. Validate a separate backup copy,
   including substitutions, unknown reps, prescriptions and pound conversions.
   Do not run an importer or point two writers at the same database.
4. Create distinct Next_Set credentials privately. Map the role of
   `BATCAVE_COACH_READ_KEY` to `NEXT_SET_COACH_READ_KEY`; never paste/copy literals
   into commands or files. The optional write role maps separately from
   `BATCAVE_SYNC_KEY` to `NEXT_SET_SYNC_KEY`. The core read API needs no sync key.
   Set `NEXT_SET_DB` to the staging copy and choose a free localhost port.
   Configure `NEXT_SET_COACH_API_URL` consistently in API/client setup.
5. Prepare Next_Set's own personal plugin and `LocalAppData/NextSet/project.json`
   only during the approved migration. Its default cached launcher still targets
   port 8787: an alternate staging port must be supplied explicitly with `-ApiUrl`
   in a staging-only client configuration, then verified from the cached copy.
   Keep Batcave's binding/cache intact. Never overwrite them with Next_Set files.
6. Validate all six tools and a real Desktop exercise-name-to-history question.
   Complete the startup and optional adapter checks in OPERATIONS.md. Switching
   the browser requires separate configuration, headers and exactly one enabled
   userscript; the generic API cannot replace the adapter's sync listener.
7. Only after parity and reboot checks pass, schedule an approved cutover. Stop
   the old writer before switching any writer; retain backups and old settings.
   No destructive migration or cutover has been executed by this release work.

Rollback: disable Next_Set tasks/plugin and stop only its owned listeners;
restore the original Batcave launch/binding and one original userscript. Batcave's
original database was never migrated in place. If staging acquired new data,
retain it privately for reconciliation instead of overwriting either database.
