# Phase 2.1 observation hardening evidence

Review date: 2026-07-22

Base branch: `main`

Base commit: `c637b28c31ab30ca1c51b3967981b574c1da281d`

Implementation commit: `5e5739681ff731c64e75342bdcd998b7519dc0cd`

Final decision: **GO**. GitHub Actions run
[`29896504186`](https://github.com/sontakmtp-cell/autocad-fastmcp/actions/runs/29896504186)
passed the complete Phase 2 Gateway workflow.

## Scope and confirmed findings

The review read the current Phase 0–3 plans/evidence and inspected the local and
`phase3_poc` composition paths before editing. The following findings were
confirmed in the base code:

- revision hashed the public observation, so `summary` omitted geometry and
  could disagree with `detail`;
- detail used unbounded per-entity `entity.get` calls;
- local snapshots had no TTL/count/aggregate-byte bound and artifact lookup
  scanned every snapshot;
- preview trusted the first attachment and labelled it PNG;
- an empty Origin allowlist was fail-open and Host wildcard matching used a
  prefix comparison;
- safe MCP errors omitted the correlation ID;
- non-object entity rows were skipped and unsupported objects were stringified;
- missing `has_document` defaulted a local device to online;
- filter elements and cursor content were not fully bounded/canonicalized.

## Implemented hardening

### Canonical revision `cad.revision/1`

The revision payload is independent from FastMCP and contains only:

```json
{
  "revision_schema": "cad.revision/1",
  "document_identity": {
    "device_id": "...",
    "document_name": "..."
  },
  "drawing": {},
  "entities": []
}
```

Entities are normalized and sorted by stable entity ID before canonical JSON and
SHA-256. Every local observation obtains entity detail for revision calculation;
`observation_level` controls only what geometry is retained in the public
snapshot. Entity layer, allowlisted geometry, text/content, dimension value and
block attributes participate when supplied by the backend. Snapshot ID,
correlation ID, observation level, timestamp and preview data do not participate.
Duplicate entity IDs and any ID/type/layer mismatch between list and detail
responses fail closed, because such a response cannot support a consistent
optimistic-concurrency revision.

Tests cover level independence, LINE movement, CIRCLE center/radius changes,
layer changes, add/remove, backend order, annotation text and unchanged repeated
observations.

### Observation budgets

Local defaults and hard upper bounds are:

| Environment variable | Default | Hard upper bound |
| --- | ---: | ---: |
| `AUTOCAD_MCP_MAX_OBSERVATION_ENTITIES` | 1,000 | 10,000 |
| `AUTOCAD_MCP_MAX_ENTITY_DETAIL_CALLS` | 1,000 | 10,000 |
| `AUTOCAD_MCP_OBSERVATION_TIMEOUT_SECONDS` | 15 | 120 |
| `AUTOCAD_MCP_MAX_SNAPSHOT_BYTES` | 4 MiB | 64 MiB |
| `AUTOCAD_MCP_MAX_IMAGE_BYTES` | 5 MiB | 20 MiB |

All limits are validated at startup and cannot be disabled with zero/negative or
unbounded values. Deadline enforcement uses `asyncio.wait_for` for Python 3.10
compatibility. A failed observation returns `observation_too_large`,
`observation_budget_exceeded`, `response_too_large` or another safe error before
the complete snapshot/artifact is added.

### Local snapshot and artifact lifecycle

The local store is independent from FastMCP and uses deterministic oldest-first
eviction:

| Environment variable | Default | Hard upper bound |
| --- | ---: | ---: |
| `AUTOCAD_MCP_SNAPSHOT_TTL_SECONDS` | 900 | 86,400 |
| `AUTOCAD_MCP_MAX_SNAPSHOT_COUNT` | 128 | 10,000 |
| `AUTOCAD_MCP_MAX_SNAPSHOT_STORE_BYTES` | 64 MiB | 512 MiB |

Only a fully constructed immutable record is admitted. Expiry and eviction
remove the artifact index entry atomically. Artifact lookup is O(1) through an
artifact-to-snapshot index and still checks `principal.subject`. Expired and
cross-owner IDs both return `not_found`. `GatewayServices.shutdown()` clears the
local store. Duplicate snapshot/artifact IDs are rejected before eviction, so
immutable IDs cannot replace an existing record or evict unrelated data. Phase
3 durable shutdown/SQLite persistence is unchanged.

### Preview, network guard and errors

- Preview selects an attachment whose MIME is exactly `image/png`, validates
  base64, non-empty decoded bytes, PNG signature and decoded size, then stores
  the verified bytes. Raw attachments and paths never enter public errors.
- Requests without Origin remain valid for native/local clients. An Origin
  header is rejected unless it matches an explicit canonical HTTP(S) origin.
  Host parsing supports exact IPv4, `localhost` and bracketed IPv6 authorities;
  suffix/prefix and malformed-port attacks fail before FastMCP session logic.
- FastMCP middleware establishes correlation context for in-memory and HTTP tool
  and resource calls. Domain, Pydantic/FastMCP validation and unexpected errors
  expose only safe code/summary plus `correlation_id`. Unexpected exceptions are
  logged with the same ID and no drawing payload, token or attachment.

### Backend data, device state and cursor policy

- Entity rows must be objects with bounded non-empty ID/type/layer. JSON values
  accept only finite primitives and bounded lists/dictionaries to depth 16;
  NaN, Infinity, foreign objects and malformed layer rows fail closed without
  materializing a snapshot.
- Local state is online only when status/health confirm reachability, an active
  document and no busy/modal condition. Missing `has_document` no longer means
  online. File IPC requires a live health/dispatcher result, so stale cached
  `active_document` state cannot assert online. Ezdxf uses its explicit
  `has_document` plus successful health.
- Entity types are trimmed, uppercased, sorted and deduplicated. Layers are
  trimmed, sorted and deduplicated with case-sensitive matching retained for
  contract compatibility. Durable Phase 3 query matching canonicalizes stored
  entity types at comparison time, preserving mixed-case Agent compatibility.
  Per-item/count/aggregate-byte limits are enforced.
- `cad.cursor/1` is base64url JSON containing snapshot ID, bounded offset and a
  SHA-256 filter hash. Snapshot/filter mismatch, malformed/negative/oversized or
  out-of-range offsets return `invalid_request`. A MAC is deferred because this
  profile is local, authenticated-by-loopback and read-only; validation prevents
  a cursor from crossing snapshot/filter boundaries.

## Public contract and Phase 3 impact

- Local remains exactly three tools, four resource templates and two prompts at
  `cad.mcp/1.0`.
- `phase3_poc` remains exactly four tools and five resource templates at
  `cad.mcp/1.1`; `cad_get_job`, SQLite, WebSocket routing, simulator and job state
  machine are unchanged.
- Checked-in Phase 2 and Phase 3 schema snapshots have no diff. Runtime
  validators were intentionally tightened without changing field names, output
  shapes or the frozen public JSON schemas.
- `composition.py` was necessarily changed to inject new budgets only into the
  local service. `durable_services.py` was necessarily changed because both
  profiles share the bounded cursor helper; no durable storage/job behavior was
  changed.
- Durable Phase 3 currently receives `document_revision` from the simulated
  Agent result. Enforcing `cad.revision/1` inside a real Desktop Agent is deferred
  to the Phase 4 read-only Agent work; this hardening does not pretend the POC
  Agent revision is locally recomputed by the Gateway.

## Verification performed

Reference runtime: Linux, Python 3.12.13.

| Gate | Result |
| --- | --- |
| Gateway Phase 2: `pytest -q -m "not phase3"` | **51 passed** |
| Gateway Phase 3: `pytest -q -m phase3` | **28 passed** |
| Exact `fastmcp==3.4.4` import assertion | passed |
| Gateway wheel | `autocad_fastmcp_gateway-0.2.0-py3-none-any.whl` built |
| Shared contracts wheel | `autocad_contracts-0.1.0-py3-none-any.whl` built |
| Simulated Agent tests | **1 passed** |
| Simulated Agent wheel | `autocad_phase3_simulated_agent-0.1.0-py3-none-any.whl` built |
| Phase 0 spike | **51 passed** |
| Root legacy/CAD Core suite | **378 passed**, 9 existing warnings |
| Gateway schema snapshot diff | clean |
| Python compileall | passed for Gateway source and tests |
| `git diff --check` | clean after documentation update |
| Hosted Gateway matrix | **6/6 passed**: Ubuntu/Windows × Python 3.10/3.12/3.13 |
| Hosted contracts/simulator job | passed |
| Hosted legacy/Phase 0 regression job | passed |

The exact root `uv run pytest tests/ -q` test command passed 378/378 after
setting `UV_PROJECT_ENVIRONMENT` to a temporary writable virtualenv. The managed
workspace's project-local virtualenv points through a read-only rewritten path;
moving only the disposable environment did not change repository files or the
test command semantics.

The local environment only provides Python 3.12. Hosted run `29896504186`
provided the cross-platform evidence for Ubuntu/Windows across Python 3.10,
3.12 and 3.13. Every matrix job passed Phase 2 and Phase 3 separately, exact
FastMCP verification, Gateway wheel build and schema drift verification. The
contracts/simulator and legacy/Phase 0 jobs also passed. The workflow now
triggers on pull requests, pushes to `main` and manual dispatch without relying
on real AutoCAD.

## Remaining risks and deferred decisions

- Summary observations now pay the entity-detail cost required for a
  concurrency-safe revision. Large drawings fail at the configured budget; a
  future Desktop Agent may add a bounded batch-detail primitive without changing
  the public MCP facade.
- Local state is process memory by design. SQLite remains exclusive to Phase 3;
  object storage, Redis, Postgres and multi-worker lifecycle are deferred.
- `cad.revision/1` covers normalized fields the current backend supplies, not a
  complete future Scene Graph or every AutoCAD entity property.
- Cursor signing/MAC, production Auth0, Desktop Agent, CAD Program, public write,
  preview-approval-commit and production deployment remain out of scope.
- A real Desktop Agent and real AutoCAD remain future integration gates; they
  are not prerequisites for this local/read-only Phase 2.1 hardening decision.
