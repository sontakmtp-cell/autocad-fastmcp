# Phase 8 implementation and acceptance evidence

> Status: implementation in progress. This record does not claim Phase 8
> Engineering GO until every core Definition of Done row below has direct
> automated and live evidence.

## Baseline

- Branch: `docs/refresh-phase-8-after-phase-7`
- Phase 8 start commit: `b33b06af356c5296d35916ab5669eab31bc11d75`
- Baseline run: 2026-07-28, Windows x64
- Toolchain: .NET SDK 8.0.423, uv 0.9.26, Node 24.18.0, npm 11.16.0
- AutoCAD write target: Managed .NET R25 / AutoCAD Mechanical 2025
- Test drawing reserved by the repository owner: `drawing33.dwg`

The local Gateway and Desktop Agent environments use a non-editable path
dependency for `autocad-contracts`. Before regression runs, rebuild that local
package with `uv sync --reinstall-package autocad-contracts`; otherwise an old
wheel can produce false contract failures after a branch change.

## Slice 8.0 baseline regression

| Surface | Command | Result |
|---|---|---|
| Root Phase 0-5 regression | `uv run pytest -q --basetemp <repo-temp>` | 409 passed, 1 skipped |
| Shared contracts | `uv run --with pytest pytest -q` | 65 passed |
| Gateway | `uv sync --group test --reinstall-package autocad-contracts`; `uv run pytest -q --basetemp <repo-temp>` | 237 passed |
| Desktop Agent | `uv sync --group test --group ui-test --reinstall-package autocad-contracts`; `uv run pytest -q --basetemp <repo-temp>` | 131 passed |
| Managed Host Core | `dotnet test AutocadMcp.ManagedHost.sln --no-restore --configuration Release` | 50 passed |
| Web Portal unit/component | `npm test` | 35 passed |
| Web Portal browser E2E | `npm run test:e2e` | 10 passed |
| Web Portal production build | `npm run build` | succeeded |

The default Windows pytest temp root was locked by another process during the
first run and caused cleanup to return exit code 1 after tests completed.
Rerunning with an explicit repository-local `--basetemp` completed with exit
code 0. This was an environment cleanup failure, not an assertion failure.

## Delivery-slice evidence

| Slice | Required evidence | Current status |
|---|---|---|
| 8.0 | Phase 0-7 regression, Phase 7 live-evidence backfill, public contract snapshot, checkpoint-v1 freeze, compiler/effect ADR | Regression green; remaining records under review |
| 8.1 | Strict v1 source, bounded AST/repeat, sealed plan/effect digests, Python/C# golden parity, no write | Pending integration |
| 8.2 | At least one create-equivalent pack on R25 with preview, approval, receipt, checkpoint-v1 ownership and rollback | Pending integration and live evidence |
| 8.3 | Immutable snapshot/query refs, prior outputs, patch/rebase lineage and in-flight protection | Pending integration |
| 8.4 | Host-generated checkpoint/restore v2 POC for allowlisted entities with atomicity and drop matrix | Pending integration |
| 8.5 | Exact transform pack with checkpoint v2, validation, Phase 7 recovery and live rollback | Pending integration |

## Phase 8 core Definition of Done audit

| Requirement | Authoritative proof required | Status |
|---|---|---|
| Strict `cad.program/1.0` | Schema/model rejection tests and malicious-input vectors | Not yet proven |
| Deterministic `cad.execution-plan/1` | Repeatable compiler output plus Python/C# golden digests | Not yet proven |
| Bounded variables, expressions and repeat | Depth/node/magnitude/expansion limit tests | Not yet proven |
| Exact source/compiler/plan/effect approval binding | Gateway intent/consent tests and Agent/Host mismatch rejection | Not yet proven |
| Live create-equivalent operation pack on R25 | Mechanical 2025 preview/commit/receipt/rollback record | Missing |
| Immutable refs and patch/rebase | Owner/device/document/revision isolation and in-flight mutation tests | Not yet proven |
| Transform with checkpoint v2 | Host pre-image/restore evidence, validation, fault matrix and live rollback | Missing |
| No primitive public tools | Phase 8 tool/resource snapshot comparison | Not yet proven |
| Unsupported tuple fails `capability_missing` | Gateway, Agent and Host negative tests | Not yet proven |
| Phase 7 guarantees do not regress | Full automated suites and recovery/drop matrix | Baseline only |
| `cad.program/0.2` and LT read do not regress | Existing v0.2 suites and LT negative matrix | Baseline only |
| Arbitrary code/path/command remains impossible | Independent security review and executable rejection tests | Not yet proven |
| Cross-runtime claims are evidence-scoped | Operation/version/entity/runtime fixture ledger | Not yet proven |

## Safety state

- Phase 8 effect-bearing feature flags remain default-off.
- Delete, trim, extend, fillet, chamfer, join and explode are not enabled.
- LT write is not enabled.
- Checkpoint v1 remains create-owned and is not accepted as a modify/delete
  rollback guarantee.
- Existing public MCP tools remain the compatibility boundary; no primitive
  public tool has been added.

