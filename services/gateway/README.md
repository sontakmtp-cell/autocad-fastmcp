# AutoCAD FastMCP Gateway — public v1

This package is the Phase 2 local, read-only facade. The legacy root package remains the default.

```powershell
$env:AUTOCAD_MCP_INTERFACE = "public_v1"
$env:AUTOCAD_MCP_BACKEND = "ezdxf"
uv run --project services/gateway --locked python -m autocad_gateway
```

The server binds to `127.0.0.1:8765`, exposes `/healthz` and `/mcp`, and publishes exactly three read tools. Set `AUTOCAD_MCP_PUBLIC_V1_DXF_PATH` before startup to load a local DXF fixture.

For the compatibility launcher, run `scripts/run-local.ps1`. `legacy` is the default; `dual` is rejected.

## Phase 3 POC

The durable Gateway is opt-in and does not change the local Phase 2 default. It uses a separate SQLite file and fixture-only Agent tokens:

```powershell
$env:AUTOCAD_MCP_GATEWAY_PROFILE = "phase3_poc"
$env:AUTOCAD_MCP_PHASE3_DB_PATH = "$PWD\phase3.db"
$env:AUTOCAD_MCP_PHASE3_FIXTURE_TOKENS = "device-a=token-a;device-b=token-b"
uv run --project services/gateway --locked python -m autocad_gateway
```

Run the independent simulator in another terminal:

```powershell
uv run --project poc/phase3-simulated-agent --locked python -m autocad_phase3_sim_agent `
  --url ws://127.0.0.1:8765/agent/ws --device-id device-a --token token-a
```

This profile is a loopback/test fixture only. It does not change the OAuth production launcher, configure a public certificate, or connect to AutoCAD.
