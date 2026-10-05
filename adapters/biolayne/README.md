# Optional BioLayne adapter

Unofficial; not affiliated with or endorsed by BioLayne. No access permission is granted by this repository. Confirm applicable terms and obtain permission for your intended integration use before enabling automated retrieval or sharing program content with a third party.

This directory owns the BioLayne token flow, upstream HTTP requests, snapshot normalization, and browser userscript. The default Next_Set API and MCP bridge never import it.

Offline import of authorized inputs:

```powershell
.\.venv\Scripts\python.exe sync.py --workout data/workout.json --master data/catalog.json --db data/training-v2.db
```

Live adapter: install `requirements.txt` from this directory, configure separate `NEXT_SET_SYNC_KEY` and `NEXT_SET_COACH_READ_KEY` secrets, then explicitly run `python -m uvicorn adapters.biolayne.app:app --host 127.0.0.1 --port 8787 --workers 1`. Run either this app or the default read-only app on that port, never both. The sync header is `X-Next-Set-Key`. No credentials or browser settings migrate automatically.

See [browser setup](tampermonkey/README.md), [sync behavior](../../SYNC_API.md), and [import semantics](../../SYNC_V2.md). Real workouts and catalogs belong only in ignored local data directories. Never publish their snapshots or paid program content.

The upstream interface is unofficial, may change, and has no compatibility guarantee. Other providers are not yet implemented. Automated sync and the renamed userscript still require live validation; unit tests alone do not establish browser compatibility.
