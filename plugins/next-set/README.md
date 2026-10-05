# Next_Set

Optional Windows desktop plugin for the local, read-only Next_Set MCP server.
Uses Agent Plugins 1.0 root `plugin.json`, `mcp.json`, and `skills/`.
No database, workout snapshot, credential, or duplicate server is bundled.

## Prepare and install

From the project checkout, run:

```powershell
.\scripts\prepare-coach-plugin.ps1
```

This copies only the five package files to `~/.codex/plugins/next-set`,
adds an AVAILABLE personal entry to `~/.agents/plugins/marketplace.json`,
and writes the non-secret checkout path to
`%LOCALAPPDATA%/NextSet/project.json`. It preserves unrelated marketplace
entries. It does not install or enable the plugin in Desktop.

First run `configure-coach-key.ps1` interactively to set the new Windows User
read key. The API must load the same `NEXT_SET_COACH_READ_KEY`; the demo's
process-only key is not automatically available to this launcher. This does
not migrate any existing plugin or credentials.

Keep the Next_Set API running at `http://127.0.0.1:8787`. Fully quit and
reopen ChatGPT Desktop on this same Windows machine/account. In Work or Codex,
open the Plugins Directory, select **Next_Set Local**, open **Next_Set**,
and install it. Enable it in a new conversation and ask what workout is next.
No OAuth or hosted MCP URL is needed. This local package is not a Mac or web
deployment. If a personal marketplace already exists, use its existing label.

The default personal marketplace is discovered automatically; a marketplace
CLI registration command is not required. To explicitly register the prepared
personal marketplace root if needed:

```powershell
codex plugin marketplace add "$env:USERPROFILE"
```

## Launch behavior

Desktop caches installed plugins. The launcher therefore resolves the existing
checkout via the external non-secret binding, not via the cache's parent folders
or the caller's working directory. Move the checkout only after rerunning the
preparation script from its new location. Reprepare and reinstall/refresh the
Desktop plugin after changing package files so the installed copy is updated.

The launcher runs the existing `.venv/Scripts/python.exe` with `mcp/server.py`
as a module over STDIO. It passes an OS-variable allowlist plus non-secret
settings. Neither Next_Set credential value is inherited by the Python child;
the maintained server reads only the coach key from Windows User registry
storage. This does not constitute isolation from other code running as the
same Windows user. API errors do not trigger synchronization.

## Validation

```powershell
.\.venv\Scripts\python.exe -B .\scripts\validate-coach-plugin.py
.\.venv\Scripts\python.exe -B .\scripts\validate-coach-plugin.py --smoke
```

The validator retrieves the declared public JSON schemas and validates both
manifests. The smoke option requires the existing API and calls all six tools
through the package's actual configured command. It emits only pass/fail.

Format and installation reference:
https://developers.openai.com/plugins/build/plugins
