# Phase 3.1 — Durable job freshness, reconnect and capability lifecycle hardening

> Status: implementation and local verification complete; **NO-GO pending
> hosted CI**. This is still a POC hardening phase, not production readiness.
>
> Baseline branch: `main`
>
> Baseline commit: `b90644645cfd5dee68ae4eb3b3ce3d1300af5bb6`
>
> Hardening branch: `agent/phase3.1-durable-lifecycle-hardening`

## Scope

Phase 3.1 hardens the existing Phase 3 POC before a real Desktop Agent depends
on its SQLite jobs, reconnect protocol, capability manifest, and immutable
snapshots. It retains the existing boundaries:

```text
FastMCP facade
    -> application services
    -> durable job/state/repository
    -> Agent transport
    -> simulated Agent
```

SQLite remains the durable source of truth. The connection registry continues
to contain only ephemeral sockets and presence. This phase does not implement a
real Desktop Agent, AutoCAD integration, production pairing/Auth0, public CAD
write, CAD Program, primitive exposure, Redis, or PostgreSQL.

## Baseline verification

The clean baseline was verified on Linux/Python 3.12 before source changes:

| Gate | Baseline result |
| --- | ---: |
| Gateway | 79 passed |
| Phase 0 compatibility | 51 passed |
| Root legacy/CAD Core | 378 passed, 9 warnings |
| Simulated Agent | 1 passed |
| Gateway, Phase 0, root, contracts and simulator wheels | built |
| Schema snapshot diff | clean |
| `git diff --check` | clean |
| Compile and lockfile checks | passed |

The managed runner injects a SOCKS proxy without the optional `socksio`
dependency. Loopback HTTP tests were therefore run with proxy environment
variables removed; this changes neither repository dependencies nor test
semantics.

## Confirmed initial findings

Independent reviews of durable semantics/public MCP, SQLite/state transitions,
and WebSocket/reconnect/simulator all reached **NO-GO** on the baseline:

1. `cad_observe` derives idempotency from request content and can reuse a
   terminal snapshot forever instead of making a fresh observation.
2. MCP wait timeout transitions the durable job to `failed`, discards a valid
   late result, and does not return the `job_id` to the caller.
3. A second caller overwrites the first job waiter, while the normal dispatch
   path can send an already-dispatched command again.
4. Snapshot insertion and terminal transition use separate transactions;
   invalid finalization can leave an orphan snapshot. Terminal jobs can still
   accept progress, and conflicting terminal results are silently ignored.
5. Reconnect/cancel transitions omit terminal cancellation and can indirectly
   requeue a started write. Cancel intent is lost across disconnect.
6. ACK `duplicate`, `rejected`, and `already_terminal` have no behavior, and
   Agent messages are not fully bound to the current session/device/job/command.
7. Heartbeats update only RAM. SQLite can report a device offline while the
   registry reports its socket fresh, and session replacement is not a single
   durable activation operation.
8. Hello capabilities and the Agent-supplied hash are discarded. Seeded static
   capabilities are exposed publicly and dispatch does not enforce them.
9. The migration runner is hard-coded to `0001_phase3.sql`; `executescript()`
   can commit partial DDL before the history row, and a failed open leaves the
   database object appearing open.
10. The maintenance task is unsupervised, so `/readyz` remains ready after a
    fatal maintenance failure.
11. Simulator reconnect scenarios only close their one socket and exit. The
    ledger is not exercised across sessions, cancellation cannot interrupt a
    running command, and most scenario names have no durable assertions.
12. Durable simulator revision generation differs from Phase 2.1
    `cad.revision/1`, and summary observations expose geometry that the local
    profile omits.
13. Agent error text can cross the public MCP boundary unsanitized, while the
    advertised artifact resource has no durable fail-closed implementation.

## Locked design decisions

- A Phase 3-only optional idempotency identity distinguishes an explicit retry
  from a new observation. Omission creates a new identity for every MCP call.
- MCP request wait timeout and durable job deadline are separate values. A wait
  timeout returns safe `job_in_progress` information containing the job ID and
  never terminalizes the job.
- One shared, shielded completion future coordinates all callers for a job.
  Only the winner of `queued -> dispatched` CAS may send the normal command.
- Snapshot/result/terminal state/event are finalized in one SQLite transaction.
- Reconciliation is evidence-driven. Only `not_started` permits redispatch;
  `started` never does. Terminal success/failure/cancellation is authoritative.
- A reconnect retains cancellation intent. Unknown write outcome is never
  changed to a retryable state without terminal evidence.
- Gateway canonicalizes and hashes bounded capabilities, persists the active
  manifest, and checks the required capability immediately before dispatch.
- Migrations are immutable, ordered, checksummed files. Phase 3.1 changes use an
  additive migration rather than editing `0001_phase3.sql`.
- `/healthz` remains liveness-only. `/readyz` includes DB/migration health and
  supervised maintenance health, but does not require an Agent to be online.
- Local `cad.mcp/1.0` retains its exact three-tool contract and semantics.
  Phase 3 remains a POC and is not declared production-ready.

## Implemented durable semantics

### Observation freshness and idempotency

`cad_observe` in `phase3_poc` has one additive optional
`idempotency_key` field. If it is omitted, the Gateway creates a fresh internal
identity for that MCP invocation. The same device, observation level and preview
flag therefore create a new job and snapshot every time. If a caller repeats an
explicit identity, the repository reuses only the job bound to the same
kind/effect/payload fingerprint; reusing the key with different input fails with
`idempotency_conflict`.

This is request deduplication, not content caching. `document_revision` remains
the `cad.revision/1` hash of drawing state, so two fresh snapshots may correctly
share a revision when the drawing did not change. `cad_query` still reads one
immutable snapshot by ID and never refreshes it implicitly.

### Request wait versus durable deadline

The MCP wait budget and durable deadline now have separate configuration:

| Setting | Meaning | Timeout outcome |
| --- | --- | --- |
| `AUTOCAD_MCP_PHASE3_REQUEST_WAIT_TIMEOUT_SECONDS` | How long one MCP invocation waits | safe `job_in_progress` error containing `job_id` and `job_state` |
| `AUTOCAD_MCP_PHASE3_JOB_DEADLINE_SECONDS` | How long the durable operation may remain non-terminal | `deadline_expired` through maintenance policy |

The wait uses a shielded shared future. Caller timeout does not cancel the
future or mutate SQLite. A late valid Agent result can still atomically complete
the job and can be read with `cad_get_job`.

### Completion coordination and atomic finalization

- All callers for one job share one completion future; a caller timeout cannot
  cancel another caller's wait.
- Only the winner of repository CAS `queued -> dispatched` sends the normal
  command. Normal dispatch returns without sending for every later state.
- Redispatch exists only after reconcile evidence `not_started` moves
  `reconnect_pending -> queued`.
- Agent result finalization validates active session, device, job, command,
  payload hash, message order, state and agent sequence inside the repository.
- Snapshot insertion, result storage, terminal state/version update and terminal
  event insertion share one SQLite transaction. Observe success cannot use the
  generic transition API to bypass this transaction.
- An identical duplicate terminal result is a no-op. A conflicting duplicate is
  rejected as `terminal_result_conflict`; late progress is ignored before it can
  append an event. Non-finite or oversized JSON is rejected.

### Reconnect, cancellation and ACK policy

Reconciliation is deliberately asymmetric because retrying an already-started
write is unsafe:

| Durable state | Agent evidence | Resulting policy |
| --- | --- | --- |
| `reconnect_pending` | `not_started` | requeue and CAS-dispatch once; if cancel intent exists, become `cancelled` without dispatch |
| `reconnect_pending` | `started` | read becomes `running`; write becomes `outcome_unknown`; neither is redispatched |
| `reconnect_pending` | terminal succeeded/failed/cancelled | atomically finalize that terminal outcome |
| `outcome_unknown` | `started` | remain unknown; resend durable cancel intent if present; never retry |
| `outcome_unknown` | `not_started` | contradictory evidence becomes `needs_attention`; never retry |
| `outcome_unknown` | terminal succeeded/failed/cancelled | atomically finalize the evidence-backed outcome |

Cancellation intent is a durable timestamp/event. Queued work cancels locally;
active work becomes `cancel_requested`; recovery states retain their state and
the intent so reconnect policy is not lost. Result/cancel races use SQLite CAS:
the terminal result may win, including success before cancellation
acknowledgement, and terminal state is immutable.

ACK statuses are explicit: `accepted` advances only the expected state,
`rejected` fails normal dispatch with `agent_rejected`, and `duplicate` or
`already_terminal` trigger ledger reconciliation when the Gateway lacks the
corresponding evidence. Exact wire replays are sequence-deduplicated before a
second durable event is created.

## Session and capability lifecycle

The registry owns only current socket identity, send serialization, heartbeat
freshness and a bounded sequence-fingerprint window. SQLite owns session history,
device status and the active capability manifest.

- A valid hello manifest is bounded and canonicalized; the Gateway recomputes
  its SHA-256 and rejects a mismatched Agent-supplied hash.
- Session activation atomically disconnects the previous durable session,
  stores canonical capabilities/hash and marks the device online.
- Valid heartbeat updates durable `last_heartbeat_at`, monotonically advances
  `last_sequence` and recovers a stale device to online.
- Stale detection uses connection identity, not only device ID. A stale session
  A snapshot cannot mark a fresh replacement B offline.
- A delayed disconnect callback for A cannot close B's session or device status.
- Startup marks pre-restart sessions disconnected, while preserving and
  recovering non-terminal jobs.
- Dispatch checks the current Agent manifest immediately before sending and
  fails with `capability_missing` without a command when the required capability
  is absent.

Fixture tokens remain header-only and available only in `phase3_poc`. The local
profile has no enabled Agent transport. This phase does not add production
device credentials or signed package manifests.

## Migration and readiness lifecycle

The migration runner discovers numbered `*.sql` files, sorts by numeric version,
checks every applied checksum, applies only missing files, and records each file
in the same transaction as its schema changes. Missing or modified applied files
fail startup. Reopening an up-to-date database is a no-op and backup/restore
preserves migrations, jobs, events, snapshots, sessions and capabilities.

`0001_phase3.sql` is unchanged. Additive `0002_phase31.sql` adds capability
hash/manifest state, request fingerprints, Agent sequence, durable cancel intent
and the one-active-session-per-device index. Shared `autocad-contracts` is bumped
to `0.1.1` so non-editable path wheels cannot reuse the old protocol model.

The maintenance loop distinguishes transient SQLite busy/locked errors from
fatal failures. `/healthz` remains process liveness. `/readyz` verifies the open
database, immutable migration history and a live supervised maintenance task;
it does not require an online Agent. Shutdown cancels and joins maintenance,
waiters and socket closes.

## Simulator behavior

The independent simulator now reconnects with bounded exponential backoff and
keeps its in-process command ledger and monotonically increasing sequence across
sessions. Hello sends `last_processed_sequence`; reconcile reports
`not_started`, `started`, or full terminal evidence with the Gateway descriptor's
job/command/payload hash. Only reconciled `not_started` enables redispatch, and
an already-started command is never executed a second time.

The full named failure matrix executes assertions, with reconnect, stale,
terminal cancellation and duplicate/order cases crossing real loopback
WebSockets. The standalone process E2E starts the packaged simulator in an
isolated environment. Fixture mutation changes `document_revision`; summary
results remove geometry while revision still uses full geometry. The simulator
imports neither Gateway, FastMCP nor AutoCAD code and exposes no HTTP/MCP server.

## Public contract delta

The local Phase 2 snapshots are byte-for-byte unchanged. The only public tool
schema delta is the optional bounded `idempotency_key` on Phase 3
`cad_observe`; its output schema, the other four Phase 3 tools/resources, all
prompts and all local schemas are unchanged. An in-progress observe returns a
safe MCP error with a pollable job ID rather than exposing stack traces or Agent
error text. Owner-scoped job, snapshot and resource lookups still fail closed as
`not_found`.

## Local verification summary

| Gate | Result |
| --- | ---: |
| Gateway Phase 2 regression | 57 passed |
| Gateway Phase 3/3.1 | 109 passed |
| Gateway total | 166 passed |
| Simulated Agent | 33 passed |
| Root legacy regression | 378 passed, 9 dependency warnings |
| Phase 0 compatibility | 51 passed |
| CAD Core package | 1 passed |
| Contracts, Gateway, simulator, root, Phase 0 and CAD Core | wheel + sdist built |
| Compile, package isolation, six lockfiles, `git diff --check` | passed |
| Snapshot review | one approved additive Phase 3 input hash; local/Phase 0 unchanged |

The hosted Ubuntu/Windows by Python 3.10/3.12/3.13 matrix remains the final gate
and is recorded in the evidence document after the pull request run completes.

## Remaining POC limits

No real Desktop Agent, AutoCAD connection, production device credential,
Auth0/device pairing, signed capability package, public TLS/VPS deployment,
multi-worker dispatcher, Redis/PostgreSQL, artifact store or public CAD write is
implemented here. SQLite and the in-process registry assume one Gateway writer.
Capabilities negotiate operation names, not production package provenance.
`outcome_unknown` may deliberately require operator attention rather than an
automatic answer.

## Acceptance gates

The authoritative implementation/test/CI evidence is recorded in
`phase3.1-durable-lifecycle-hardening-evidence.md`. All implementation and local
gates are green. Phase 4 remains blocked until the hosted OS/Python matrix is
green and linked from that record.
