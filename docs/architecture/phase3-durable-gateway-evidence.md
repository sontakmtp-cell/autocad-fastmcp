# Phase 3 durable Gateway evidence

Date: 2026-07-21

Status: implementation verified locally; production TLS, VPS, real Agent credentials and AutoCAD remain intentionally out of scope.

## Verified

- `uv run --project services/gateway --locked pytest -q` from `services/gateway`: **50 passed**.
- `test_phase3_mcp_flow.py`: live Uvicorn loopback MCP flow, two-device routing, job/event/resource flow, two standalone simulator subprocesses, additive snapshots, and a real `wss://localhost` connection using a generated test certificate: **4 passed**.
- `uv run --project poc/fastmcp-phase0 --locked pytest -q`: **14 passed**.
- Root legacy suite: **376 passed, 1 skipped, 9 warnings**.
- `uv run --project poc/phase3-simulated-agent --locked --group test pytest -q`: **1 passed**.
- `uv build` succeeded for `packages/contracts`, `services/gateway`, and `poc/phase3-simulated-agent`.
- `git diff --check`: passed.

## Behaviour demonstrated

1. `phase3_poc` seeds two fixture devices in SQLite and reports them online only after their outbound Agent sessions complete `hello`/`welcome`.
2. `cad_observe(device-a)` creates a durable job, dispatches only to device A, accepts ordered progress/result messages, persists a snapshot, and exposes `cad_get_job`, `cad_query`, and the job resource.
3. Idempotency uses a canonical payload hash; the same request reuses the row and a changed payload is rejected.
4. CAS claims, owner filtering, event cursors, SQLite WAL/foreign keys, migration checksum checks, backup/restore, restart recovery, and stale/deadline hooks are covered by tests.
5. A read job can return to `queued` after a reconnect reports `not_started`; a started write-like fixture becomes `outcome_unknown` and dispatch refuses to retry it.

## Not verified in this phase

- The end-to-end pytest flow includes both an in-test WebSocket fixture for deterministic assertions and a live run that launches two separate standalone simulator subprocesses through the CLI.
- The real production OAuth/Auth0 flow, public domain certificate, reverse proxy, VPS deployment, device pairing/key rotation, Desktop Agent, AutoCAD/COM/File IPC and CAD write operations were not run.
- CI configuration is prepared in `.github/workflows/phase2-gateway.yml`; the GitHub matrix itself was not executed on this workstation.

The POC must remain behind the explicit `phase3_poc` profile. `local` remains the default, and the OAuth production launcher and legacy entrypoints are unchanged.
