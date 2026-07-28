# Phase 8 implementation and acceptance evidence

> Status: **NO-GO**. The round-two conformance suite intentionally exits
> non-zero while the canonical golden, mandatory release binding, and actual
> R25 dispatcher-registration blockers below remain unresolved.

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
| 8.1 | Strict v1 source, bounded AST/repeat, sealed plan/effect digests, Python/C# golden parity, no write | Runtime compiler is `1.1`; checked-in golden is still `1.0`, so parity gate fails |
| 8.2 | At least one create-equivalent pack on R25 with preview, approval, receipt, checkpoint-v1 ownership and rollback | Pending integration and live evidence |
| 8.3 | Immutable snapshot/query refs, prior outputs, patch/rebase lineage and in-flight protection | Pending integration |
| 8.4 | Host-generated checkpoint/restore v2 POC for allowlisted entities with atomicity and drop matrix | Pending integration |
| 8.5 | Exact transform pack with checkpoint v2, validation, Phase 7 recovery and live rollback | Pending integration |

## Phase 8 core Definition of Done audit

| Requirement | Authoritative proof required | Status |
|---|---|---|
| Strict `cad.program/1.0` | Schema/model rejection tests and malicious-input vectors | Automated contract evidence |
| Deterministic `cad.execution-plan/1` | Repeatable compiler output plus Python/C# golden digests | **Failing:** current compiler output does not match the checked-in canonical golden |
| Bounded variables, expressions and repeat | Depth/node/magnitude/expansion limit tests | Automated |
| Exact source/compiler/plan/effect approval binding | Gateway intent/consent tests and Agent/Host mismatch rejection | **Failing:** an otherwise valid Phase 8 release is accepted when mandatory intent and consent binding rows are absent |
| Live create-equivalent operation pack on R25 | Mechanical 2025 preview/commit/receipt/rollback record | Missing |
| Immutable refs and patch/rebase | Owner/device/document/revision isolation and in-flight mutation tests | Owner-scoped guessed-ID rejection and canonical materialized/evidence digest recomputation pass; patch/rebase remains incomplete |
| Transform with checkpoint v2 | Host pre-image/restore evidence, validation, fault matrix and live rollback | Missing |
| No primitive public tools | Phase 8 tool/resource snapshot comparison | Automated snapshot plus denylist |
| Unsupported tuple fails `capability_missing` | Gateway, Agent and Host negative tests | Contract and Host Core negatives automated; live R25 dispatch remains unproven |
| Phase 7 guarantees do not regress | Full automated suites and recovery/drop matrix | Baseline only |
| `cad.program/0.2` and LT read do not regress | Existing v0.2 suites and LT negative matrix | Baseline only |
| Arbitrary code/path/command remains impossible | Independent security review and executable rejection tests | Automated contract evidence |
| Cross-runtime claims are evidence-scoped | Operation/version/entity/runtime fixture ledger | Automated catalog; live rows remain missing |

## Round-two conformance integration

Command:

```powershell
python scripts\test-phase8-conformance.py
```

Current evidence:

- the conformance suite invokes the real canonical compiler, but fails because
  its `1.1` output and digests do not match the checked-in `1.0` golden fixture;
- Gateway owner scoping rejects cross-owner guessed IDs, and canonical
  materialized-reference and capability-evidence digests are recomputed rather
  than trusted from callers;
- the mandatory Phase 8 intent-and-consent release gate currently fails:
  release can proceed when both binding rows are absent;
- `cad.agent/2` serializes the shared plan, execution binding and capability
  evidence, and Desktop admission consumes those canonical wire artifacts;
- the Phase 7 MCP profile remains byte-identical to its frozen snapshot. The
  Phase 8 profile has only the documented `cad_prepare_program` input/output
  schema delta and adds no primitive public tool;
- arbitrary code, command, path, UNC path, URL, environment, script, raw
  handles, excessive expression depth and repeat expansion fail closed;
- Managed Host Core JSON/checkpoint-v2 tests pass, but the actual R25 dispatcher
  does not yet call `Phase8ManagedOperationPack`; the registration gate fails;
- no `xfail` marker hides these blockers. The aggregate command remains
  deliberately red until production integration and canonical fixtures agree.

The GitHub Actions gate runs only portable Python and Managed Host Core tests.
It does not build a standalone Desktop Agent executable and does not require
Autodesk references. `scripts/build-phase8-r25-host.ps1` is the separate local
Windows/R25 path and requires installed AutoCAD 2025 references.

## Safety state

- Phase 8 effect-bearing feature flags remain default-off.
- Delete, trim, extend, fillet, chamfer, join and explode are not enabled.
- LT write is not enabled.
- Checkpoint v1 remains create-owned and is not accepted as a modify/delete
  rollback guarantee.
- Existing public MCP tools remain the compatibility boundary; no primitive
  public tool has been added.
