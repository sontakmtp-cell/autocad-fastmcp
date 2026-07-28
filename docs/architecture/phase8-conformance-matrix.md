# Phase 8 conformance and evidence matrix

Status: NO-GO. Desktop, digest recomputation, profile snapshots and Host Core
contracts are green; canonical golden parity, mandatory unbound-release
rejection, actual R25 dispatcher registration and live R25 evidence are red.

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
| S8-010 public MCP tools/resources | Phase 7 snapshot + Phase 8 delta + snapshot test | Automated | Phase 7 is byte-identical; Phase 8 changes only the documented `cad_prepare_program` schema hashes |
| S8-010 sensitive/primitive denylist | `test_public_surface_snapshot.py` | Automated | Recursive input-schema properties expose no owner/risk/runtime/handle/ObjectId/restore/capability/approval/command/path/URL authority; primitive and approval tool aliases are absent |
| Source/compiler malicious input | `test_canonical_compiler_conformance.py` | Automated | Real strict source/compiler rejects code, command, path, UNC, URL, environment, script, raw handle, depth and expansion abuse |
| Python/C# digest parity | Canonical compiler test + checked-in golden + Host contract test | Failing | Runtime compiler is 1.1 while the checked-in golden is 1.0; compiler and plan digests differ |
| Gateway sealed storage/binding | `test_cross_stack_acceptance.py` | Blocked by golden mismatch | Real storage path executes, but cross-stack acceptance requires the same canonical fixture |
| `cad.agent/2` serialization | `test_cross_stack_acceptance.py` | Failing fixture assertion | Canonical fields exist, but checked-in execution-plan digest is stale |
| Desktop admission | `test_cross_stack_acceptance.py` | Automated | Shared canonical plan/binding/evidence are verified and only sealed Host arguments are emitted |
| Mandatory intent + consent binding | `test_security_integration_gates.py` | Failing | Phase 8-shaped release material without Phase 8 binding rows is currently accepted |
| Cross-owner guessed IDs | `test_security_integration_gates.py` | Automated | Foreign owner receives `not_found`/no plan for guessed plan, intent and consent IDs |
| Materialized/evidence digest recomputation | `test_security_integration_gates.py` | Automated | Repository recomputes canonical materialized and capability-evidence digests and rejects tampering |
| Actual R25 dispatcher registration | `test_host_dispatch_registration.py` | Failing | Pack exists but no registered dispatcher invokes it |
| Cross-runtime fixture categories | `cross-runtime-categories.json` | Automated catalog validation | Claim axes, negative categories, and authority boundaries are explicit |
| LT write default-off | `test_lt_write_default_off.py` | Automated | Current Gateway and Agent defaults/rejections remain fail-closed |
| ezdxf non-authoritative | Cross-runtime catalog and tests | Automated policy assertion | No fixture can promote ezdxf to live DWG authority |
| Fault/drop/recovery | Matrix + Gateway tests + Host transaction tests | Mixed automated/live pending | Atomic abort, duplicate mismatch and sealed storage are automated; real disconnect points still need live evidence |
| Rollout/capability gates | `rollout-capability-matrix.json` | Automated catalog validation | Security-review slice decisions, default-off flags, capability states and disabled extension packs are explicit |
| Checkpoint v1 boundary | Contract test + cross-runtime rules | Automated | v1 has created-entity evidence, not pre-image/restore-v2 data |
| Checkpoint v2 / exact transform | Host contract suite + `transform-checkpoint-v2-matrix.json` | Contract automated; live pending | Strict restore JSON, atomic transaction, duplicate safety and capability closure are executable; R25 fidelity remains unproven |
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
| `phase8-cross-stack-conformance` | 8.0-8.7 | Gateway `.venv` | No |
| `phase8-host-json-checkpoint-v2` | 8.2-8.5 | .NET SDK/cache | No |

List or run the matrix:

```powershell
python scripts\test-phase8-conformance.py
.\scripts\test-phase8-regression.ps1 -ListOnly
.\scripts\test-phase8-regression.ps1 -Suite contracts-phase6-7,gateway-phase0-7
```

`python scripts/test-phase8-conformance.py` is the canonical single-command
check. It runs Python through the isolated Gateway environment and Managed Host
Core through .NET. It does not add FastMCP to the root dependency graph and
does not require Autodesk assemblies.

## Integration gates still blocked

The following cannot be green on the current baseline and must not be reported
as complete:

- compiler 1.1/Python/C# golden regeneration and parity;
- fail-closed recognition of Phase 8 release material before binding rows exist;
- actual registered Host command routing into the Phase 8 operation pack;
- create-equivalent R25 geometry/receipt/checkpoint/rollback conformance;
- checkpoint v2 restore fidelity and atomic fault matrix;
- exact transform preview/commit/validate/rollback matrix;
- Phase 8 live Mechanical 2025 acceptance;
- any LT write certification;
- any delete/trim/fillet/chamfer extension claim.

Each remaining row must gain an executable adapter, test command,
runtime/package hashes, and retained evidence location before its status
changes. There are no expected-failure markers in the conformance suite; these
gates fail normally until production integration resolves them.
