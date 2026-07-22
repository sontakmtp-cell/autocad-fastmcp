# Phase 1.1 — CAD Core packaging, contract and adapter parity hardening evidence

> Final status: **GO**
>
> Hardening branch: `phase1.1-cad-core-hardening`
>
> Base revision: `e321fd0fffd3a0c6fe80edddd234795756693538`
>
> Implementation and CI revision: `30921d758c29ea9d12af8d1d4acb48a38031be7e`
>
> Evidence date: 2026-07-22

## 1. Initial findings

The root wheel declared a dependency on the generic distribution name
`cad-core`, while uv resolved that name only through a source-checkout path.
The wheel itself therefore did not prove where an installer would obtain the
dependency. The generic name also left room for resolving an unrelated public
distribution. CAD Core isolation was guarded only by AST checks. Finally,
`CadRuntimePort.call(operation, *args)` remained the main path for the read
operations needed by the future Desktop Agent.

## 2. Packaging decision

Phase 1.1 uses **two independent wheels built from one revision**:

- `autocad_mcp-3.0.0-py3-none-any.whl`;
- `autocad_cad_core-0.1.0-py3-none-any.whl`.

The Python import remains `cad_core`. The distribution was renamed from the
generic `cad-core` to the project-specific `autocad-cad-core`, and the root
dependency is pinned to `autocad-cad-core==0.1.0`. This prevents an installer
from silently satisfying the dependency with an unrelated distribution and
lets Phase 4 install or test the core contract independently.

Bundling was not selected because the legacy facade, Phase 0 facade, Gateway,
and future Desktop Agent consume the same core seam independently. A separate
wheel keeps that boundary executable rather than merely architectural and is
still a small, low-risk change. No package is published by this phase.

## 3. Wheel and artifact structure

The reference artifact produced by GitHub Actions run `29890677706` has digest:

```text
sha256:8e0022747a42246f108649255f0c00f5f0a6bd2e2f077c87730faf4d126de6f4
```

Inspection of that artifact proved:

- `autocad_cad_core-0.1.0-py3-none-any.whl` contains `cad_core` and does not
  contain `autocad_mcp`;
- `autocad_mcp-3.0.0-py3-none-any.whl` contains `autocad_mcp`, does not bundle
  `cad_core`, and declares `Requires-Dist: autocad-cad-core==0.1.0`;
- both wheels require Python 3.10 or newer;
- both wheels are built from the same checked-out revision.

## 4. Clean-install proof

`scripts/phase1_1_packaging_smoke.py` builds a local wheelhouse, downloads the
root wheel's public transitive dependencies, then creates virtual environments
outside the repository. It removes `PYTHONPATH`, sets `PYTHONNOUSERSITE=1`,
runs from a temporary directory, installs with `--no-index --find-links`,
verifies module origins are inside the clean environment, imports
`autocad_mcp` and `cad_core`, and instantiates `CadApplicationService` with a
fake typed port.

A second environment installs only `autocad-cad-core`. It proves that
`autocad_mcp`, MCP, FastMCP, Starlette, win32com, and pythoncom are absent, then
exercises both a typed read call and the explicitly retained compatibility
fallback.

The clean-install matrix passed on:

```text
Ubuntu  : Python 3.10, 3.12, 3.13
Windows : Python 3.10, 3.12, 3.13
```

The reference core wheel was also installed and exercised manually in a fresh
Python 3.13 virtual environment outside the checkout; `cad_core.__file__`
resolved from that environment's `site-packages`.

## 5. Typed read contract

`CadReadPort` defines explicit methods for:

- `system.status` / `system.get_backend` -> `get_status()`;
- `system.health` -> `health()`;
- `drawing.info` -> `get_drawing_info()`;
- `entity.list` -> `list_entities(layer=...)`;
- `entity.get` -> `get_entity(entity_id=...)`;
- `layer.list` -> `list_layers()`;
- `view.get_screenshot` -> `get_screenshot()`.

`CadApplicationService` exposes matching typed methods. Legacy invocations for
these operations are routed to those methods, preserving the public contract
while preventing Phase 4/public read code from knowing backend method names or
positional argument order.

`CadRuntimePort.call()` remains only as a documented compatibility fallback
for write operations and legacy primitives not migrated in Phase 1.1. No new
mega-enum, mega-model, MCP dependency, FastMCP dependency, Starlette dependency,
COM implementation, AutoLISP implementation, or File IPC implementation was
added to `cad_core`.

## 6. Adapter parity

`poc/fastmcp-phase0/tests/test_dual_adapter_parity.py` is a test-only harness.
One shared fake runtime and one `CadApplicationService` are used by a legacy
compatibility adapter and a typed public-facade adapter. The harness covers:

- drawing information;
- entity listing;
- screenshot success and attachment preservation;
- backend failure;
- unexpected exception;
- unknown operation;
- missing required field;
- invalid screenshot base64;
- screenshot above the configured size limit;
- health success and health failure.

The runtime records calls. Tests assert one backend call per request, prevent
typed reads from falling through generic dispatch, compare normalized
`CommandResult` and error categories, and separately verify transport-specific
formatting and intended public-contract differences. No production dual mode
or production environment flag was added.

## 7. Core independence

Static import checks remain. `packages/cad_core/tests/test_standalone.py` adds
package-local runtime tests. GitHub Actions runs those tests from the package's
own locked environment on Ubuntu and Windows, builds the standalone wheel, and
repeats the proof in a clean environment containing only the CAD Core wheel.

The runtime proof requires neither `autocad_mcp`, MCP, FastMCP, Starlette,
pywin32, nor a real AutoCAD installation.

## 8. Public contracts and Phase 2–3 impact

The 16 legacy tool names, decorators, signatures, descriptions, annotations,
input schemas, compact JSON formatter, `TextContent`, `ImageContent`, screenshot
validation, `ONLY_TEXT_FEEDBACK`, OAuth scopes, remote policy, audit,
host/origin rules, unknown-operation response, and health error mapping were
not intentionally changed. The frozen descriptor snapshot passed in every
Phase 0 matrix job.

Public v1 models, tool/resource/prompt schemas, Gateway job state, SQLite
schema, Agent protocol, ownership isolation, idempotency, reconnect,
`outcome_unknown`, and snapshot/revision semantics are unchanged. The `local`
and `phase3_poc` profiles remain unchanged.

## 9. Test results

JUnit evidence from GitHub Actions run `29890677706`:

| Suite | Pass | Fail | Error | Skip |
| --- | ---: | ---: | ---: | ---: |
| Root legacy and CAD Core regression | 378 | 0 | 0 | 0 |
| Standalone CAD Core | 1 | 0 | 0 | 0 |
| Phase 0 FastMCP and dual-adapter parity | 51 | 0 | 0 | 0 |
| Gateway Phase 2–3 | 50 | 0 | 0 | 0 |
| Phase 3 simulated Agent | 1 | 0 | 0 | 0 |
| **Total reference suites** | **481** | **0** | **0** | **0** |

Additional matrix/build evidence:

- root `Test` workflow run `29890677692`: **SUCCESS**;
- Phase 1.1 workflow run `29890677706`: **SUCCESS**;
- clean-install matrix: 6/6 jobs passed;
- Phase 0 compatibility matrix: 6/6 jobs passed;
- standalone CAD Core: Ubuntu 3.12 and Windows 3.12 passed;
- root, CAD Core, Phase 0, Gateway, shared contracts, and simulated Agent wheel
  builds passed;
- `git diff --check`, `python -m compileall`, and every relevant
  `uv lock --check` passed.

The repository's older dedicated Phase 0 and Gateway workflow runs for the same
revision were still queued when this evidence was finalized. They are not
counted as passes. Their required test/build commands were executed successfully
inside the completed self-contained Phase 1.1 workflow above, so the decision
does not rely on a queued result.

## 10. CI changes

`.github/workflows/phase1-1-cad-core-hardening.yml` now provides:

- wheel clean-install on Ubuntu/Windows with Python 3.10/3.12/3.13;
- standalone CAD Core tests/builds on Ubuntu and Windows Python 3.12;
- Phase 0 FastMCP/parity tests/builds/snapshot checks on the same six-entry
  compatibility matrix;
- one full-regression job for root, CAD Core, Gateway, contracts, and simulated
  Agent;
- JUnit and wheel artifacts;
- static, compile, diff, and lockfile checks.

The existing Phase 0 matrix was not reduced. Existing root and Gateway
workflows were not weakened.

## 11. Files changed

The implementation touches the packaging metadata and lockfiles, CAD Core
contracts/tests, legacy/Phase 0/Gateway adapters, packaging smoke script,
parity tests, CI workflows, and the Phase 1/architecture documents. It does not
move unrelated modules or implement Phase 4 runtime features.

The authoritative changed-file list is the branch diff against base revision
`e321fd0fffd3a0c6fe80edddd234795756693538`.

## 12. Remaining risks before Phase 4

- Write operations still use compatibility string dispatch and should be
  migrated incrementally when concrete Phase 4 write commands are designed.
- The two wheels are prepared as local artifacts only; release signing,
  repository hosting, installer integration, and updater policy remain Phase 4
  or later work.
- Hosted wheel tests do not replace a real Windows AutoCAD runtime test.
- Version coordination between the two wheels is exact but still manual; Phase
  4 distribution work should build, sign, publish, and install both artifacts
  as one release set.

## 13. Final decision

**GO**

Phase 1.1 meets its packaging, typed read seam, adapter parity, core isolation,
legacy contract, Phase 0–3 regression, build, clean-install, lockfile, and CI
acceptance gates. Phase 4 Desktop Agent work may begin without migrating the
remaining write compatibility fallback in this phase.
