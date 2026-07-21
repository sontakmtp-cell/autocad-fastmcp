# FastMCP Phase 0 facade spike

This is an isolated compatibility project. It does not replace the legacy
server and it does not expose AutoCAD or write operations.

The facade exposes exactly three tools:

- `cad_list_devices`
- `cad_observe`
- `cad_get_job`

It also exposes the two bounded resource templates described by the Phase 0
plan. The fake service uses an in-memory `EzdxfBackend` fixture and creates a
fresh store for each test.

## Run locally

From this directory:

```powershell
uv sync --locked --group test
uv run --locked --group test pytest -q
```

The lockfile is intentionally separate from the root project. The root
legacy suite remains runnable from the repository root.

Snapshot tests compare against `snapshots/` and never rewrite it. Any future
snapshot update must be an explicit reviewed change.
