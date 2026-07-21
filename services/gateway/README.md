# AutoCAD FastMCP Gateway — public v1

This package is the Phase 2 local, read-only facade. The legacy root package remains the default.

```powershell
$env:AUTOCAD_MCP_INTERFACE = "public_v1"
$env:AUTOCAD_MCP_BACKEND = "ezdxf"
uv run --project services/gateway --locked python -m autocad_gateway
```

The server binds to `127.0.0.1:8765`, exposes `/healthz` and `/mcp`, and publishes exactly three read tools. Set `AUTOCAD_MCP_PUBLIC_V1_DXF_PATH` before startup to load a local DXF fixture.

For the compatibility launcher, run `scripts/run-local.ps1`. `legacy` is the default; `dual` is rejected.
