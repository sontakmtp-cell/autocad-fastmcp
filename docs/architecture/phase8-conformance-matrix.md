# Phase 8 conformance and evidence matrix

Status: foundation implemented; production integration and live evidence pending

## Evidence rules

- Automated tests prove only the code and fixture scope they execute.
- ezdxf may prove offline geometry or invalid-input behavior, never live DWG
  preview, commit, receipt, recovery, or rollback.
- A portable claim is keyed by operation/version/entity/runtime/release and
  evidence version. One R25 result does not imply LT or R18-R24 support.
- Missing artifacts remain missing. Owner confirmation is labeled separately
  from independently reproducible evidence.
- LT write, topology packs, and destructive packs remain disabled.
- `cad.rollback.checkpoint/1` is accepted only for create-only or
  create-equivalent output ownership. Modify/erase/topology requires
  checkpoint/restore v2 evidence.

## Foundation coverage

| Area | Artifact/test | Current status | What it proves |
|---|---|---|---|
| Phase 0-7 regression | `tests/phase8/regression-matrix.json`, `scripts/test-phase8-regression.ps1` | Ready to run per suite | Commands, environments, and phase ownership are explicit |
| S8-010 public MCP tools/resources | `public-surface-phase7.json`, `test_public_surface_snapshot.py` | Phase 7 exact; Phase 8 exact profile snapshot blocked pending profile | Exact 10-tool schemas/annotations and 16 resource templates are the Phase 7 and Phase 8 contract-freeze baseline |
| S8-010 sensitive/primitive denylist | `test_public_surface_snapshot.py` | Automated | Recursive input-schema properties expose no owner/risk/runtime/handle/ObjectId/restore/capability/approval/command/path/URL authority; primitive and approval tool aliases are absent |
| Source/compiler malicious input | `source-compiler-vectors.json` | Blocked pending compiler adapter | Path, UNC/device/traversal, URL/file URL, command/load-assembly and other required reject categories are frozen |
| Python/C# digest parity | Source/compiler catalog | Blocked pending compiler and C# runner | No digest claim yet |
| Cross-runtime fixture categories | `cross-runtime-categories.json` | Automated catalog validation | Claim axes, negative categories, and authority boundaries are explicit |
| LT write default-off | `test_lt_write_default_off.py` | Automated | Current Gateway and Agent defaults/rejections remain fail-closed |
| ezdxf non-authoritative | Cross-runtime catalog and tests | Automated policy assertion | No fixture can promote ezdxf to live DWG authority |
| Fault/drop/recovery | `fault-recovery-matrix.json` | Scaffold | Drop points, expected states, evidence, and no-reexecute invariant are enumerated |
| Rollout/capability gates | `rollout-capability-matrix.json` | Automated catalog validation | Security-review slice decisions, default-off flags, capability states and disabled extension packs are explicit |
| Checkpoint v1 boundary | Foundation test + cross-runtime rules | Automated | v1 has created-entity evidence, not pre-image/restore-v2 data |
| Phase 7 live backfill | `phase7-live-acceptance-backfill.md` | Owner-confirmed, incomplete artifacts | Preserves the claim without fabricating missing evidence |

## Regression suite matrix

| Suite ID | Phase scope | Expected environment | Live AutoCAD required |
|---|---|---|---|
| `root-phase0-5` | 0-5 | root `.venv` | No |
| `contracts-phase6-7` | 6-7 | contracts `.venv` | No |
| `host-contracts-phase6-7` | 6-7 | contracts `.venv` | No |
| `gateway-phase0-7` | 0-7 | Gateway `.venv` | No |
| `desktop-agent-phase4-7` | 4-7 | Desktop Agent `.venv` | No |
| `managed-host-core-phase6-7` | 6-7 | .NET SDK/cache | No |
| `web-portal-unit-component-phase7` | 7 | Node modules | No |
| `web-portal-e2e-phase7` | 7 | Playwright/browser | No |
| `web-portal-build-phase7` | 7 | Node modules | No |
| `phase8-conformance-foundation` | 8.0-8.7 | Gateway `.venv` | No |

List or run the matrix:

```powershell
python scripts\test-phase8-conformance.py
.\scripts\test-phase8-regression.ps1 -ListOnly
.\scripts\test-phase8-regression.ps1 -Suite contracts-phase6-7,gateway-phase0-7
```

`python scripts/test-phase8-conformance.py` is the canonical single-command
foundation check. It runs the pytest portion through the isolated Gateway uv
project and does not add FastMCP to the root dependency graph.

## Integration gates still blocked

The following cannot be green on the current baseline and must not be reported
as complete:

- exact Phase 8 profile schema snapshot after the profile exists; S8-010 uses
  the exact Phase 7 snapshot as the contract-freeze baseline until then;
- execution of `cad.program/1.0` source/compiler vectors;
- deterministic Python/C# compiler, plan, expansion, and effect digest parity;
- create-equivalent R25 geometry/receipt/checkpoint/rollback conformance;
- checkpoint v2 restore fidelity and atomic fault matrix;
- exact transform preview/commit/validate/rollback matrix;
- Phase 8 live Mechanical 2025 acceptance;
- any LT write certification;
- any delete/trim/fillet/chamfer extension claim.

When production integration lands, each blocked row must gain an executable
adapter, golden output, test command, runtime/package hashes, and retained
evidence location before its status changes.
