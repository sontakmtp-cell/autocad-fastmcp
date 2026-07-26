# Phase 5 local telemetry pilot

This collector accepts only the Phase 5 read-only telemetry contract and stores
aggregate counts and latency. It never stores raw events, identity, credentials,
document paths/content, CAD Programs, LISP, or stack traces.
Ingest also requires a random bearer token; the collector config stores only
its SHA-256 hash and each Windows Agent stores the token with DPAPI.

Use `configure-phase5-telemetry-pilot.ps1` to generate the token/config and set
`bind_host`:

- `127.0.0.1` when the Agent is on this Windows host.
- The host's private Hyper-V adapter IPv4 address when a VM Agent participates.
  In that case both the host Agent and VM Agent use that private address.

Restrict TCP port 4319 with Windows Firewall to that Hyper-V subnet. Do not
publish the port through a router, tunnel, or cloud proxy.

Use the scripts in the repository root:

```powershell
.\scripts\start-phase5-telemetry.ps1 -ConfigPath D:\AutoCAD-MCP-Telemetry\collector.json
.\scripts\test-phase5-telemetry-health.ps1 -BaseUrl http://127.0.0.1:4319
.\scripts\stop-phase5-telemetry.ps1 -StateRoot D:\AutoCAD-MCP-Telemetry
```

The dashboard is at `/dashboard`; JSON aggregates are at `/metrics`.
