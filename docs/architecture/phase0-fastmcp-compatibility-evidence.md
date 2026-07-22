# Phase 0 FastMCP facade compatibility evidence

Current review date: 2026-07-22  
Decision gate: **GO**

This document preserves the original Phase 0 baseline and separately records the
current regression status after Phase 1, Phase 2, and Phase 3 were merged into
`main`.

## Tested revision and hosted evidence

- `main` used as the hardening base: `9d70fd89d9809f3964465d544cce7b3247dc580c`
- runtime/test/workflow revision tested: `f5af74d1362403e856a93d1bd827c1f50a456a64`
- pull request: [#2](https://github.com/sontakmtp-cell/autocad-fastmcp/pull/2)
- GitHub Actions run: [29884069053](https://github.com/sontakmtp-cell/autocad-fastmcp/actions/runs/29884069053)
- Phase 0 JUnit artifact: `phase0-junit`, artifact `8515869868`, digest
  `sha256:103472e28738c414ed248899708b3ea3e9368e19f6eac1ef4b19476b7675a3a7`
- Phase 1–3 regression JUnit artifact: `phase1-3-regression-junit`, artifact
  `8515872873`, digest
  `sha256:38176bc4d15338ee174c3e099b69be11998a3a8afc662aa7c945adb9b1cc796b`

Documentation-only commits follow the tested runtime revision. The runtime,
tests, schema snapshot, lockfiles, and workflow described below are the content
of `f5af74d1362403e856a93d1bd827c1f50a456a64`.

## Locked compatibility versions

The isolated Phase 0 lock resolves:

- FastMCP `3.4.4`
- MCP SDK `1.28.1`
- Starlette `1.3.1`
- Pydantic `2.13.4`
- HTTPX `0.28.1`
- PyJWT `2.13.0`
- Authlib `1.7.2`
- Ezdxf `1.4.4`
- Pillow `12.3.0`

Each compatibility job explicitly imports FastMCP and asserts version `3.4.4`.
`uv sync --locked` and `uv lock --check` prove that no lockfile rewrite was
required.

## Current CI matrix

| OS | Python 3.10 | Python 3.12 | Python 3.13 |
| --- | --- | --- | --- |
| Ubuntu `24.04` hosted runner | passed | passed | passed |
| Windows hosted runner | passed | passed | passed |

Every one of the six compatibility jobs performed:

1. `uv sync --locked --group test`;
2. exact FastMCP `3.4.4` verification;
3. all Phase 0 tests;
4. Phase 0 wheel build;
5. schema snapshot drift check.

The same run also completed the static/isolation job and the root plus Phase 1–3
regression job.

## Test and build results

JUnit evidence from the hosted Ubuntu/Python 3.12 reference jobs:

| Suite | Result |
| --- | --- |
| Phase 0 | **40 passed**, 0 failed, 0 errors, 0 skipped |
| Root legacy and CAD Core | **377 passed**, 0 failed, 0 errors, 0 skipped |
| Gateway Phase 2–3 | **50 passed**, 0 failed, 0 errors, 0 skipped |
| Simulated Agent | **1 passed**, 0 failed, 0 errors, 0 skipped |

Build evidence:

- Phase 0 wheel: passed in all six matrix jobs;
- Gateway wheel: passed;
- shared contracts wheel: passed;
- simulated-Agent wheel: passed;
- `git diff --check`: passed;
- Python compile check: passed;
- root, Phase 0, Gateway, and simulated-Agent lock checks: passed.

## Public schema review

The public tool schema now uses the same constrained `Annotated` aliases as the
Pydantic request models. FastMCP strict input validation is enabled.

Reviewed schema diff:

- `cad_observe.device_id`: added `minLength: 1`, `maxLength: 128`;
- `cad_get_job.job_id`: added `minLength: 1`, `maxLength: 128`;
- `cad_get_job.event_cursor`: added `maxLength: 128` to the string branch;
- no tool name, resource URI, output field, enum value, or production Phase 2–3
  contract changed.

Tests reject an empty identifier, a 129-character identifier/cursor, undeclared
fields, a string passed as a boolean, an integer passed as a boolean, and an
invalid `observation_level`. Each validation failure proves that the service spy
received no call. Snapshot tests compare the checked-in schema and separately
assert that handler constraints match the contract-model constraints, detecting
future drift.

`artifact_refs` remains allowed to be empty for structured-only observations.
When `include_preview_image=true`, the handler must find and validate a PNG or
return `preview_unavailable`; it may not index the list blindly.

## Real DXF proof

The fixture still uses a real headless `EzdxfBackend` through
`CadApplicationService`; it does not read or write a DWG.

Initialization now checks the result of:

1. backend initialization;
2. `create_line`;
3. `create_circle`;
4. screenshot rendering and PNG validation.

A failed step aborts initialization with a bounded stage message and leaves no
materialized snapshot.

`cad_observe` executes both `drawing.info` and `entity.list`. It validates that
the reported counts agree, derives `entity_summary` from the actual entity list,
removes path-bearing backend fields such as `save_path`, computes a SHA-256
revision from the canonical observed state, and only then materializes an
owner-scoped snapshot and PNG artifact.

Proof tests show:

- the initial fixture produces exactly `LINE: 1` and `CIRCLE: 1`;
- a second real LINE added through `CadApplicationService` changes the summary
  to `LINE: 2`, `CIRCLE: 1` and the entity count to `3`;
- LINE creation failure aborts initialization;
- CIRCLE creation failure aborts initialization;
- invalid screenshot bytes abort initialization;
- a `drawing.info` or `entity.list` failure returns the safe `backend_error` and
  leaves the snapshot store empty.

`read_snapshot()` returns only the JSON that was previously materialized. It no
longer reconstructs fixed entity counts.

## Authentication and authorization proof

Authentication and authorization are separate boundaries:

### Authentication

`SubjectJWTVerifier`, a public `JWTVerifier` subclass, verifies signature,
issuer, audience, expiry, and token format through FastMCP, then requires a
non-empty JWT `sub`. It does not require `autocad.read` and does not use
`client_id` or `azp` as an identity fallback.

The HTTP authentication tests return `401` for:

- no token;
- wrong signature;
- wrong issuer;
- wrong audience;
- expired token;
- a signed token containing `client_id` and `azp` but no `sub`.

Raw bearer tokens are asserted absent from response text, captured logs, tool
output, and service-call records.

### Component authorization

The three tools and both resource templates use
`require_scopes("autocad.read")`. A correctly signed token with a valid `sub`
but no read scope successfully initializes the MCP session. FastMCP then hides
the protected tools/resources from list responses and returns its standard
not-found behavior for direct component access. The service spy remains empty.

A valid token with `autocad.read` can list/call tools and read an owned snapshot
resource. This records the JWT `sub`, scope, and a request correlation ID in the
service boundary.

This is deliberately recorded as framework-native component authorization; the
POC does not hard-code a synthetic `403` where FastMCP 3.4.4 uses hidden
components and not-found responses.

## Concurrent two-user proof

The concurrency test creates two separately signed JWTs with subjects `user-A`
and `user-B`. It starts two independent Streamable HTTP client sessions and uses
`asyncio.Event` plus locks inside a fake service barrier. Both requests must
arrive before either can continue; no arbitrary sleep is used.

The test proves:

- real overlap for tool requests and snapshot-resource requests;
- distinct subjects, scopes, and correlation IDs;
- no `user-A` value appears in `user-B` call records and vice versa;
- every call retains `autocad.read` without scope leakage;
- each user receives a different snapshot ID;
- a cross-user snapshot read returns not-found;
- no global mutable principal is used.

## Preview artifact failure proof

When a preview is requested, the handler selects an `image/png` reference and
validates the stored artifact wrapper, MIME type, byte type, non-empty content,
2,000,000-byte limit, and PNG signature before creating `ImageContent`.

Parameterized tests cover:

- `artifact_refs=[]`;
- missing artifact ID;
- non-byte payload;
- empty PNG;
- oversized PNG;
- artifact MIME other than `image/png`.

All six cases return `preview_unavailable`, contain no traceback or internal
artifact identifier, emit no image content, and keep the tool result in the MCP
error state rather than incorrectly reporting a successful observation.

## Static and isolation proof

The hosted static job verifies:

- no FastMCP private manager/module API in the Phase 0 adapter;
- CAD Core, shared contracts, and Gateway domain do not import `fastmcp`, `mcp`,
  or `starlette`;
- snapshot tests do not rewrite checked-in snapshots;
- no private-key or live-token fixture marker is committed in Phase 0;
- the POC retains its own lockfile and exact FastMCP pin;
- lockfiles do not drift;
- whitespace and Python syntax checks pass.

The isolation check compares current architecture rules, not a historical diff,
so valid Phase 1–3 changes are not rejected.

## Historical Phase 0 baseline

The original evidence remains historical and is not rewritten:

- root baseline commit: `7d6a78283e5e50d8b89c38d551691abcd4f4f074`;
- root result then: `302 passed, 1 skipped`;
- original Phase 0 result: `14 passed`;
- original hosted run: `29832408444`;
- original Phase 0 decision: `GO` for the isolated three-tool facade.

The old statement that the root project and `src/autocad_mcp` were not changed
was true only for that initial spike. Phase 1–3 later changed the root and legacy
implementation legitimately, so it is not used as current regression evidence.

A requested historical file named `docs/phase0-baseline.md` was checked at the
original Phase 0 implementation and evidence commits (`211d62e...` and
`d0fbb547...`) and was not present. No replacement content was invented.

The untouched `main` revision was not separately re-run as a pre-fix baseline
because the available execution environment could not install the repository
outside GitHub Actions. The defects were confirmed directly in current source,
and the historical 14-test result is retained above. The patched runtime was
then tested in the complete hosted matrix and regression gates described in this
report.

## Limits not tested

Phase 0 still does not prove:

- real AutoCAD or DWG I/O;
- Auth0 login/DCR or a real ChatGPT Web client;
- production reverse-proxy and TLS behavior;
- multi-process or multi-host snapshot storage;
- Phase 3 SQLite/WebSocket durability semantics beyond their existing Gateway
  suite;
- production authorization policy beyond `autocad.read`;
- write operations or any Phase 4 feature.

The in-memory owner check proves request/resource isolation for the spike, not a
production persistence design.

## Conclusion

**GO.** FastMCP `3.4.4` passes the six required Windows/Linux and Python
3.10/3.12/3.13 combinations. The Phase 0 facade now derives evidence from the
real DXF backend, publishes constrained strict schemas, separates JWT
verification from component scope authorization, proves deterministic two-user
isolation, rejects unsafe preview artifacts, and passes root plus Phase 1–3
regression and build gates without changing their public production contracts.
