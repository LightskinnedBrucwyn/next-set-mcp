# Moving to Next_Set

This is a separate release checkout. The original installation remains unchanged.

- Plugin and MCP server ID: `next-set`; display name: Next_Set.
- Settings: `NEXT_SET_COACH_READ_KEY`, `NEXT_SET_COACH_API_URL`, `NEXT_SET_COACH_KEY_SOURCE`, and optional adapter `NEXT_SET_SYNC_KEY`.
- Database selection: `NEXT_SET_DB`; default is this checkout's ignored `data/training-v2.db`.
- Plugin binding: Windows LocalAppData `NextSet/project.json`, created only when its preparation script is run.
- Browser storage and sync header names changed. Configure the renamed userscript separately; never run duplicate sync scripts.

There is no automatic credential migration, plugin installation, database copy, server restart, or network change. First validate with the synthetic demo. Deliberately configure private storage and credentials before changing an existing installation. Do not point both sync services at the same database.
