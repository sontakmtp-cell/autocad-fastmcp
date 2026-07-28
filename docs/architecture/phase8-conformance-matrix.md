# Phase 8 conformance and evidence matrix

Status: **GO for the bounded signed R25 lab profile**. Portable contracts,
Gateway/Agent admission, actual R25 dispatcher routing, create-equivalent
rollback and checkpoint-v2 transform restore are green. Disabled extension
packs remain NO-GO.

## Evidence rules

- Automated tests prove only the code and fixture scope they execute.
- ezdxf proves offline geometry only, never live DWG commit or rollback.
- A support claim is keyed by operation, version, entity type, runtime,
  release and evidence version.
- `cad.rollback.checkpoint/1` applies only to created-output ownership.
- Modify-in-place requires checkpoint/restore v2.
- Mixed checkpoint-v1/v2 plans fail closed until compound atomic rollback is
  implemented.
- LT write, topology and destructive packs remain disabled.

## Foundation coverage

| Area | Artifact/test | Status | Proof |
|---|---|---|---|
| Phase 0-7 regression | `scripts/test-phase8-regression.ps1` | Green | All 11 suites pass |
| Public MCP surface | Phase 7 snapshot + Phase 8 delta tests | Green | No primitive write/approval tool added |
| Strict source/compiler | `test_canonical_compiler_conformance.py` | Green | Malicious code/path/URL/command and budget abuse fail closed |
| Python/C# digest parity | Golden compiler and Host contract tests | Green | Compiler 1.1 golden and Host projection agree |
| Gateway sealed storage/binding | Cross-stack and real-compiler integration tests | Green | Exact materialized refs, intent/consent and capability evidence are sealed |
| `cad.agent/2` serialization | Cross-stack acceptance | Green | Canonical plan/binding/evidence wire artifacts round-trip |
| Desktop admission | Desktop and cross-stack suites | Green | Runtime/capability/policy mismatch rejects before Host |
| Owner isolation | Security integration gates | Green | Guessed foreign IDs return not found |
| Digest recomputation | Security integration gates | Green | Caller-supplied materialized/evidence digests are recomputed |
| Actual R25 dispatcher | `test_host_dispatch_registration.py` + R25 build | Green | Canonical preview/commit/recovery route to managed pack |
| Create-equivalent R25 | Live signed-lab drill | Green | Copy receipt, checkpoint v1, erase rollback and validation |
| Exact transform R25 | Live signed-lab drill | Green | Move receipt, checkpoint v2, pre-image restore and validation |
| Fault/drop/recovery | Host transaction tests + Gateway matrix | Green in bounded scope | Abort, duplicate, unknown outcome and recovery semantics execute |
| Mixed create/transform | Host admission test | Fail-closed | Rejected until compound rollback exists |
| LT write | Default-off tests | Disabled | No LT write claim |
| Delete/topology | Registry and public-surface denylist | Disabled | No destructive extension claim |

## Regression results

| Suite ID | Result |
|---|---:|
| `root-phase0-5` | 409 passed, 1 skipped |
| `contracts-phase6-7` | 113 passed |
| `host-contracts-phase6-7` | 9 passed |
| `gateway-phase0-7` | 247 passed |
| `desktop-agent-phase4-7` | 142 passed |
| `managed-host-core-phase6-7` | 72 passed |
| `web-portal-unit-component-phase7` | 35 passed |
| `web-portal-e2e-phase7` | 10 passed |
| `web-portal-build-phase7` | succeeded |
| `phase8-cross-stack-conformance` | 39 passed |
| `phase8-host-json-checkpoint-v2` | 11 passed |

Canonical command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-phase8-regression.ps1 -StopOnFailure
```

Portable Phase 8-only command:

```powershell
python scripts/test-phase8-conformance.py
```

Live R25 command, after loading the signed lab Host in Mechanical 2025:

```powershell
$env:PYTHONPATH = "apps/desktop_agent/src;packages/contracts/src;services/gateway/src"
python scripts/phase8-live-r25-e2e.py `
  --output tmp/phase8-live-r25-e2e.json
```

## Live evidence

- Drawing: `drawing33.dwg`
- Runtime: Managed .NET R25 / AutoCAD Mechanical 2025
- Package:
  `sha256:b30fa2d41698bc654c3bd350713ea244c64a0e26db4e876cdbbc30cdeb2ed236`
- Registry:
  `sha256:1b840d43a4872322882f4443c07fb0f0b238cbb1d122cbefb4fe7e59097024a5`
- Evidence:
  `docs/architecture/evidence/phase8-live-r25-e2e-20260728.json`

The retained artifact contains command IDs, plan/effect digests, live Host
runtime evidence, receipt/checkpoint lookups, rollback plans, rollback receipts,
validation results and before/after fingerprints. It contains no bootstrap or
session secret.

## Remaining NO-GO claims

- production certificate/timestamp and customer rollout;
- public OAuth/ChatGPT-to-Gateway acceptance;
- compound atomic rollback for mixed checkpoint strategies;
- LT write;
- delete, trim, extend, fillet, chamfer, join, explode or topology packs.
