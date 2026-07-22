# Phase 3.1 durable lifecycle hardening evidence

> Status: **NO-GO pending hosted CI**
>
> Baseline commit: `b90644645cfd5dee68ae4eb3b3ce3d1300af5bb6`
>
> Branch: `agent/phase3.1-durable-lifecycle-hardening`
>
> Implementation commit: pending
>
> Evidence date: 2026-07-22

This is the review record for Phase 3.1. It is intentionally limited to the
SQLite/single-writer Phase 3 POC and does not claim production readiness. The
architecture and remaining limits are described in
[`Phase-3.1.md`](Phase-3.1.md).

## Baseline

The branch was created from the latest fetched `main` at
`b90644645cfd5dee68ae4eb3b3ce3d1300af5bb6`; the source worktree was clean.
Before changes, Linux/Python 3.12 verification produced:

| Baseline gate | Result |
| --- | ---: |
| Gateway | 79 passed |
| Phase 0 | 51 passed |
| Root legacy | 378 passed, 9 dependency warnings |
| Simulated Agent | 1 passed |
| Five existing package wheels plus contracts | built |
| Snapshots, compile, lockfiles, `git diff --check` | clean |

## Independent review findings and root causes

Three independent subagent tracks reviewed durable/public semantics,
SQLite/state transitions, and WebSocket/simulator behavior. Their findings were
cross-checked against the mandatory docs and source listed in the Phase 3.1
request.

| Confirmed blocker | Root cause in baseline | Phase 3.1 correction |
| --- | --- | --- |
| A new observe could return an old snapshot | idempotency key was a permanent hash of device/level/preview content | omitted identity now generates a fresh request key; explicit retry identity is fingerprint-bound |
| MCP wait timeout destroyed late work | caller wait budget transitioned the durable job to `failed` | shielded wait returns `job_in_progress` with job ID; only durable deadline terminalizes |
| Concurrent retry could lose one caller and send twice | `_waiters[job_id]` was overwritten and dispatch resent non-queued work | one shared future plus repository CAS winner-only normal send |
| Snapshot could be orphaned | snapshot insert committed before success transition | one finalizer transaction writes snapshot/result/state/event or rolls all back |
| Terminal jobs mutated | progress and duplicate/conflicting results lacked terminal guards | terminal progress is ignored; exact result duplicate is idempotent; conflict is rejected/audited |
| Reconnect/cancel matrix was incomplete | cancelled terminal evidence and durable cancel intent were missing; recovery could reach retry indirectly | evidence-driven terminal cancelled handling; persistent cancel timestamp; no started-write retry path |
| ACK statuses were silent | only accepted ACK affected state | rejected/duplicate/already-terminal now fail or reconcile by explicit policy |
| Presence split between RAM and SQLite | heartbeat updated registry only; replacement callbacks used device identity | durable heartbeat/session activation plus connection-identity stale/replacement checks |
| Capabilities were decorative | Agent hash was trusted/discarded, device seed was public truth, dispatch did not gate | bounded canonical manifest, Gateway hash, SQLite persistence and pre-send capability gate |
| Simulator reconnect existed only as scenario names | one socket closed and process exited; ledger was not reconciled | bounded reconnect loop, persistent ledger/sequence and real-WebSocket scenario assertions |
| Migration runner could not accept `0002` safely | one hard-coded file and `executescript()` lifecycle | ordered immutable checksummed files and atomic schema/history transaction per migration |
| Readiness stayed green after worker death | maintenance task was unsupervised and readiness checked only a DB pointer | fatal task supervision and DB/migration/task readiness checks |
| Cached protocol wheel could stay stale | non-editable local dependency changed without package version | `autocad-contracts` bumped from `0.1.0` to `0.1.1`; dependent locks refreshed |

## Durable invariant evidence

| Invariant | Direct evidence |
| --- | --- |
| Fresh observe after fixture mutation | `test_fresh_observe_does_not_reuse_terminal_job_after_fixture_changes`; full MCP/WSS routing test mutates the live fixture and asserts new job, snapshot, revision and geometry |
| Explicit retry is idempotent | `test_concurrent_explicit_retry_shares_waiter_and_dispatches_once` asserts two callers, one command, one snapshot and the same terminal result |
| Wait timeout preserves work | `test_mcp_wait_timeout_exposes_job_id_and_late_result_succeeds` asserts in-progress state/job ID, late success and one snapshot |
| CAS normal dispatch | repository concurrent claim test and shared-caller application test each assert one winner/send |
| Atomic finalization | tests cover invalid order, stale CAS, snapshot trigger failure, terminal-update trigger failure, concurrent identical/conflicting results and no orphan rows/events |
| Terminal immutability | exhaustive domain terminal matrix plus late progress and duplicate/conflicting result repository tests |
| Reconnect/cancel | tests cover not-started redispatch, started no-redispatch, terminal success/failed/cancelled, unknown evidence, cancel/result CAS and offline cancel intent |
| ACK policy | parameterized application test covers rejected, duplicate and already-terminal follow-up/state |
| Session/heartbeat replacement | repository lifecycle test plus maintenance stale-A/replacement-B race keep B online and its running job unchanged |
| Capability lifecycle | protocol hash mismatch, canonical persistence/change and capability-missing-before-send tests |
| Migrations and restore | pending-only/rerun, changed/missing checksum, failed-DDL rollback and backup/restore tests |
| Readiness | fatal maintenance test asserts `/healthz` 200 and `/readyz` 503 |
| Owner isolation | existing job/snapshot/resource tests remain fail-closed as `not_found` |

## State machine delta

- `reconnect_pending + not_started` is the only redispatch path. Durable cancel
  intent changes this to `cancelled` without a send.
- `reconnect_pending + started` becomes `running` for read or
  `outcome_unknown` for write; it is never redispatched.
- `reconnect_pending` and `outcome_unknown` accept evidence-backed terminal
  succeeded, failed and cancelled results.
- `outcome_unknown + started` stays unknown. Contradictory `not_started`
  evidence escalates to `needs_attention`, never queue.
- Active cancellation becomes `cancel_requested`; recovery cancellation records
  intent without discarding the recovery state. A terminal result that commits
  first remains the deterministic winner.
- `succeeded`, `failed`, `cancelled` and `needs_attention` have no outgoing
  transition.

## Session, capability and protocol evidence

Hello is authenticated with an Authorization bearer token in `phase3_poc`; a
query token is rejected. `hello.device_id` must match the authenticated device
and the fixture proof. Subsequent messages must match protocol, current session,
device, job and command. Payload hashes are recomputed/bound to the durable job;
capability hashes are recomputed from the canonical bounded manifest. Exact
sequence duplicates are no-ops and conflicting/stale sequences are rejected.

Both Uvicorn and simulator use the shared 1 MiB WebSocket limit; protocol models
also bound JSON depth/container/string sizes, capabilities, reconcile batches,
result fields and human-readable messages. Agent errors are mapped to a bounded
public taxonomy; internal paths, stack traces and raw Agent messages do not cross
the MCP boundary.

## Simulator failure matrix

All required scenario names execute behavior and assertions:

```text
success
drop_before_ack
drop_after_ack_before_start
drop_after_start_before_result
reconnect_not_started
reconnect_started
reconnect_terminal
duplicate_ack
duplicate_progress
duplicate_result
out_of_order_progress
payload_hash_mismatch
stale_heartbeat
cancel_before_start
cancel_while_running
cancel_too_late
delay_before_ack
delay_result
```

Success/delay/duplicate/order/hash/cancel cases cross a real loopback WebSocket.
Drop/reconnect tests assert hello sequence continuity, ledger status and exactly
one execution. Terminal-cancelled reconciliation crosses a real WebSocket.
The Gateway suite additionally starts two simulator OS processes and completes
MCP -> SQLite -> Agent -> result -> snapshot -> job/query end to end.

## Migration evidence

`0001_phase3.sql` has no diff. `0002_phase31.sql` additively introduces:

- `devices.capability_hash`;
- session capability JSON/hash;
- job request fingerprint, last Agent sequence and cancel timestamp;
- a partial unique index allowing one active session per device.

Migration application and its history insert share one transaction. Startup
fails if an applied file changes or disappears. A second startup makes no data
change. Backup/restore retains migration state and all durable records.

## Public contract and snapshot review

The only approved schema change is additive and profile-scoped:

```text
phase3 cad_observe input:
  + idempotency_key: optional string, 1..128 characters as schema-bounded
```

`services/gateway/snapshots/phase3_tools.json` changes only the `cad_observe`
input schema SHA-256:

```text
d7a0857bb52b461eb3c19a0b26a005e660ad66c589327db3756cbc3f44b37799
->
396c4c9ac16413dbc26af13ed82c8f8db0e1b1c96ff31e731ed701fa8ece00a4
```

The Phase 3 observe output hash, all other Phase 3 tool/resource hashes, and all
local Phase 2/Phase 0 tool/resource/prompt snapshots are unchanged.

## Files changed by responsibility

- Shared wire contract: `packages/contracts/pyproject.toml`, lockfile and
  `src/autocad_contracts/{agent_protocol.py,__init__.py}`.
- Durable Gateway: public contracts/app/composition/services, job application
  service, domain state machine, SQLite database/repository/`0002`, connection
  registry/WebSocket endpoint and launcher limits.
- Simulator: CLI, reconnecting Agent, ledger/failure tests and lockfile.
- Verification: expanded Gateway domain/repository/application/transport/E2E
  tests and the additive Phase 3 schema snapshot.
- Delivery: Phase 3.1 architecture/evidence docs, master plan status and a new
  cross-platform CI workflow.

## Local verification results

All tests were rerun from newly created package environments after the shared
contract version/lock refresh.

| Suite/gate | Result |
| --- | ---: |
| Gateway non-Phase-3 (Phase 2/2.1 regression) | 57 passed |
| Gateway Phase 3/3.1 | 109 passed |
| Gateway total | 166 passed |
| Simulator | 33 passed |
| Root legacy | 378 passed, 9 dependency warnings |
| Phase 0 compatibility | 51 passed |
| CAD Core | 1 passed |
| Contracts import/smoke | passed |
| Contracts, Gateway, simulator, root, Phase 0, CAD Core | wheel and sdist built |
| Python compileall | passed |
| Static package/import boundary | passed |
| Root, CAD Core, contracts, Phase 0, Gateway, simulator locks | passed |
| `git diff --check` | clean |
| Local/Phase 0 immutable snapshots | clean |

## Hosted CI

The Phase 3.1 workflow defines Ubuntu and Windows jobs for Python 3.10, 3.12 and
3.13 plus a static/isolation/snapshot/lock job with concurrency cancellation.
It runs Gateway Phase 2-3.1, contracts, simulator, root legacy, Phase 0, builds,
compile, isolation, snapshots and all lockfiles.

No hosted run has completed for this implementation commit yet. Links and exact
job results will replace this paragraph after the draft pull request run. A
queued, skipped or partially green matrix is not counted as evidence.

## Remaining limits

- This remains fixture-token, loopback/single-writer POC infrastructure.
- No production pairing, credential rotation, Auth0 tenant, public VPS/TLS,
  multi-worker dispatch, signed package manifest or operator UI exists.
- No real Desktop Agent, AutoCAD, COM/File IPC or public CAD write is included.
- `outcome_unknown` intentionally favors `needs_attention` over guessing.
- Capability negotiation proves bounded lifecycle/enforcement, not package
  provenance or production authorization.

## Verification ledger and decision

| Gate | Result |
| --- | --- |
| Clean baseline | passed |
| Targeted durable/application/transport/simulator tests | passed |
| Full local regressions | passed |
| All requested package builds | passed |
| Snapshot review | passed; one approved additive Phase 3 input hash |
| Compile/isolation/lock/whitespace | passed |
| Hosted Ubuntu/Windows × Python 3.10/3.12/3.13 | pending |
| Phase 4 read-only decision | **NO-GO pending hosted CI** |

Phase 4 must not start until every hosted matrix job is green and linked here.
