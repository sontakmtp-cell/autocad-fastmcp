# Phase 5 local telemetry pilot evidence

Status 2026-07-26: **engineering complete; live host + VM transport and
fail-open pilot passed; 3–7 day soak pending**.

## Implemented

- Agent-side read-only instrumentation with a bounded, non-blocking queue.
- Strict custom HTTP ingest endpoint at `/ingest/autocad-mcp`.
- Authenticated ingest with a random token, collector-side hash and Agent-side
  DPAPI protection.
- Loopback/private-IP-only exporter configuration.
- Aggregate-only collector and local dashboard.
- Bounded series cardinality and bounded request body.
- Exporter drop/error counters in a safe local diagnostics JSON file.
- Host start/stop/health/firewall scripts and a VM runbook.
- Privacy contract tests matching
  `native/autocad_managed_host/packaging/phase5-runtime-policy.json`.

## Automated evidence

Run:

```powershell
uv run --project apps/desktop_agent --group test pytest `
  apps/desktop_agent/tests/test_telemetry.py `
  apps/desktop_agent/tests/test_telemetry_collector.py -q
```

Covered cases:

- exact allowlisted dimensions and measures;
- policy drift detection;
- no command payload or identity in emitted telemetry;
- public and OTLP-shaped endpoint rejection;
- write operation rejection;
- unknown/sensitive field rejection;
- queue overflow and collector outage fail open;
- aggregate-only persistence;
- bounded series;
- real local HTTP ingest and dashboard response.
- missing or invalid ingest token rejected with HTTP 401.

## Live evidence

- Collector health passed at `http://172.20.96.1:4319/health`.
- Device A on the physical host completed a public ChatGPT
  `drawing.observe.summary` against `drawing33.dwg`: 30 entities, read-only.
- Device B in the VM has no AutoCAD. Its presence telemetry reports the bounded
  `autocad_not_running` safe error instead of fake runtime readiness.
- The aggregate contains:
  - `autolisp_file_ipc / compatibility_fallback /
    agent.presence.health / succeeded`;
  - `autolisp_file_ipc / compatibility_fallback /
    agent.presence.health / failed / autocad_not_running`;
  - `autolisp_file_ipc / compatibility_fallback /
    drawing.observe.summary / succeeded`.
- The live drawing observation incremented the last series to `count=1`; the
  Agent exporter reported `accepted=4`, `dropped=0`, `rejected=0`,
  `export_errors=0`.
- A token-rotation mismatch intentionally made the collector reject exports.
  During that condition, another public observation still returned the same
  30 entities and revision while exporter `export_errors` increased. This is
  live fail-open evidence.
- Restarting the elevated collector loaded the rotated token and new aggregates
  arrived without restarting AutoCAD.
- Dashboard/aggregate inspection found no owner, device id, account, drawing
  name/path, command payload, token or raw CAD data.

The configuration script now refuses to rotate the pilot token while a
collector is running. Agent provisioning also pins telemetry runtime labels to
the actual AutoLISP/File IPC compatibility path instead of the managed-host
defaults.

## Operator evidence still required

- Agent/Host restart, revoke/re-pair, busy/modal, and rollback drills.
- 3–7 day pilot summary with success rate, latency, disconnect/reconnect, and
  safe error totals.

Do not mark production telemetry complete from the automated evidence alone.
