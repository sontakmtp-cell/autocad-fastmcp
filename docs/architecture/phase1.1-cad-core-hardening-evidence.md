# Phase 1.1 — CAD Core packaging, contract and adapter parity hardening evidence

> Status while the implementation branch is being verified: **PENDING HOSTED CI**
>
> Hardening branch: `phase1.1-cad-core-hardening`
>
> Base revision: `e321fd0fffd3a0c6fe80edddd234795756693538`

## 1. Initial findings

The root wheel declared a dependency on the generic distribution name
`cad-core`, while uv resolved that name only through a source-checkout path.
The wheel itself therefore did not prove where an installer would obtain the
dependency. CAD Core isolation was also guarded only by AST checks. Finally,
`CadRuntimePort.call(operation, *args)` remained the main path for the read
operations needed by the future Desktop Agent.

## 2. Packaging decision

Phase 1.1 uses **two independent wheels from one revision**:

- `autocad_mcp-3.0.0-py3-none-any.whl`;
- `autocad_cad_core-0.1.0-py3-none-any.whl`.

The Python import remains `cad_core`. The distribution was renamed from the
generic `cad-core` to the project-specific `autocad-cad-core`, and the root
dependency is pinned to `autocad-cad-core==0.1.0`. This avoids accidentally
resolving an unrelated public distribution and lets Phase 4 install or test
the core contract independently. Bundling was not selected because Gateway,
the Phase 0 facade, and the future Desktop Agent all consume the same core
seam independently; a separate wheel keeps that boundary executable rather
than merely architectural.

No package is published by this phase.

## 3. Clean-install proof

`scripts/phase1_1_packaging_smoke.py` builds a local wheelhouse, downloads
the root wheel's public transitive dependencies, then creates virtual
environments outside the repository. It removes `PYTHONPATH`, runs from a
temporary directory, installs with `--no-index --find-links`, verifies module
origins are inside the clean environment, imports `autocad_mcp` and
`cad_core`, and instantiates `CadApplicationService` with a fake typed port.

A second environment installs only `autocad-cad-core`. It proves that
`autocad_mcp`, MCP, FastMCP, Starlette, and pywin32 are absent, then exercises
both a typed read call and the explicitly retained compatibility fallback.

## 4. Typed read contract

`CadReadPort` defines explicit methods for:

- `system.status` / `system.get_backend` -> `get_status()`;
- `system.health` -> `health()`;
- `drawing.info` -> `get_drawing_info()`;
- `entity.list` -> `list_entities(layer=...)`;
- `entity.get` -> `get_entity(entity_id=...)`;
- `layer.list` -> `list_layers()`;
- `view.get_screenshot` -> `get_screenshot()`.

`CadApplicationService` exposes matching typed methods. Legacy invocations
for these operations are routed to those methods, preserving the public
contract while preventing Phase 4/public read code from knowing backend
method names or positional argument order.

`CadRuntimePort.call()` remains only as a documented compatibility fallback
for write operations and legacy primitives not migrated in Phase 1.1.

## 5. Adapter parity

`poc/fastmcp-phase0/tests/test_dual_adapter_parity.py` is a test-only harness.
One shared fake runtime and one `CadApplicationService` are used by a legacy
compatibility adapter and a typed public-facade adapter. The harness covers
drawing information, entity listing, screenshots, backend failures,
unexpected exceptions, unknown operations, missing fields, invalid and
oversized base64 images, and health success/failure. It records backend
calls, blocks typed operations from falling through generic dispatch, and
distinguishes domain parity from transport-specific formatting.

No production dual mode or environment flag was added.

## 6. Core independence

Static import checks remain. `packages/cad_core/tests/test_standalone.py` adds
package-local runtime tests, and the wheel smoke test repeats the proof in a
clean environment containing only the CAD Core wheel.

## 7. Public contracts and Phase 2–3 impact

The 16 legacy tool decorators, signatures, descriptions, annotations,
compact JSON formatter, `TextContent`, `ImageContent`, screenshot validation,
OAuth, remote policy, audit, host/origin rules, and error mappings are not
modified. The frozen descriptor snapshot remains the drift gate.

Public v1 models, tool/resource/prompt schemas, Gateway job state, SQLite
schema, Agent protocol, ownership isolation, idempotency, reconnect,
`outcome_unknown`, and snapshot/revision semantics are unchanged. Local and
`phase3_poc` profiles remain unchanged.

## 8. Test and CI results

This section is finalized only from hosted GitHub Actions evidence. Until
then, no pass result is claimed.

| Suite / check | Result |
| --- | --- |
| Root legacy + CAD Core | PENDING |
| Standalone CAD Core | PENDING |
| Phase 0 FastMCP | PENDING |
| Gateway Phase 2–3 | PENDING |
| Simulated Agent | PENDING |
| Root / core / contracts / Gateway / Agent wheel builds | PENDING |
| Clean install Ubuntu/Windows | PENDING |
| Lock, compile and diff checks | PENDING |

## 9. Remaining risks before Phase 4

- Write operations still use compatibility string dispatch and should be
  migrated incrementally when concrete Phase 4 write commands are designed.
- The two wheels are prepared as local artifacts only; release signing,
  repository hosting, installer integration, and updater policy remain Phase 4
  or later work.
- Hosted wheel tests do not replace a real Windows AutoCAD runtime test.

## 10. Decision

**PENDING** until hosted CI and final diff review complete.
