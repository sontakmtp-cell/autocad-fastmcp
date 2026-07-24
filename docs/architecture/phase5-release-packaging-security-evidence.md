# Phase 5 release-family, LT, identity and runtime-policy evidence

Status: **R25 lab signing plus isolated install/upgrade/rollback evidence**.
This document does not certify deferred AutoCAD release families, AutoCAD LT,
a publicly trusted production certificate, a fresh clean-VM run, or a
multi-user write pilot.

## Implemented boundaries

### Release families and local packaging

`native/autocad_managed_host/packaging/phase5-release-families.json` is the
machine-readable support claim. It keeps the architecture's four build
families separate:

| Family | Intended releases | Target | Current evidence | Write |
|---|---|---|---|---|
| R22 | AutoCAD 2018 | .NET Framework 4.6 | Not built; real 2018 lab required | Off |
| R23 | AutoCAD 2019–2020 | .NET Framework 4.7 | Not built; real 2019 and 2020 matrix required | Off |
| R24 | AutoCAD 2021–2024 | .NET Framework 4.8 | Not built; real 2021 and 2024 matrix required | Off |
| R25 | AutoCAD 2025–2026 | .NET 8 Windows | Mechanical 2025 read-only plus create-only transaction/restart-reconcile POC; 2026 still required | Off by release policy |

`PackageContents.phase5.xml` is a multi-component bundle scaffold with an exact
relative DLL path and non-overlapping `SeriesMin`/`SeriesMax` for each family.
Every component is Windows x64 and uses `Platform="AutoCAD|ACADM"`; no LT
component exists.

`scripts/build-phase5-release-bundle.ps1` only stages artifacts already produced
by a controlled builder. It fails unless all four exact family DLL names exist,
creates SHA-256 inventory data, and writes only below repository `dist`.
It neither downloads SDKs nor uploads/publishes artifacts.

`scripts/install-phase5-release-bundle.ps1` is deliberately lab-only. It:

- requires explicit `-LabOnly`;
- validates family, runtime policy, XML, relative paths and every artifact hash;
- installs only to the current user's Autodesk `ApplicationPlugins` directory;
- moves an existing bundle to a timestamped backup;
- writes a local receipt containing the exact backup and destination;
- states that the result is unsigned and is not production certification.

Rollback is an operator action while AutoCAD is closed: remove the just-installed
lab bundle and move the exact backup recorded in the receipt back to the fixed
destination. Production rollback remains blocked until signing, installer
transaction tests, clean-VM tests and recovery tests are complete.

The R25-specific signed release path now supersedes that manual procedure for
R25 acceptance. It signs both Host assemblies and the install/rollback scripts,
binds the signed installer to the exact release-manifest hash, verifies every
artifact hash and signer, preserves a previous-known-good backup, and uses a
versioned rollback receipt. The local isolated clean install, upgrade,
exact-hash restore and clean-install rollback rehearsal passed. See
`phase5-signing-clean-vm-runbook.md`.

### LT compatibility

The manifest fixes LT to `autolisp_file_ipc`, sets
`managed_host_loaded=false`, and records `automated_regression_only`. The
existing SafeFileIPC/packaged AutoLISP code is not changed by packaging work.
The dedicated automated matrix remains in
`phase5-lt-regression-matrix.md`.

This is not LT certification. A fresh AutoCAD LT 2024+ device must still prove:

- package/dispatcher load and public read-only observe;
- allowlist rejection and absence of arbitrary LISP;
- busy/modal/document-change handling;
- install, upgrade and rollback;
- no Managed .NET component loaded or registered.

Portable CAD Program on LT is not enabled by this scaffold.

### Runtime policy and telemetry

`phase5-runtime-policy.json` is a fail-closed lab policy:

- `managed_read` and `lt_read` are on;
- `managed_write`, `lt_write`, `high_risk`, `advanced_lisp`,
  `arbitrary_code`, and runtime fallback are off;
- telemetry is limited to runtime/release/operation/outcome/safe-error
  dimensions and count/latency measures;
- owner identity, credentials, pipe secret, document path/content, raw LISP,
  CAD Program payload and stack traces are prohibited.

The validator refuses risky switches that default on. This file is policy
scaffolding, not a production policy distribution service and not permission
to open broad write.

### Identity isolation and safe diagnostics

Automated evidence adds two narrow checks:

1. A durable job result containing Managed Host runtime/package evidence is
   visible to its owner and returns `not_found` semantics to another owner.
   Cross-owner job creation against that device is denied.
2. Agent diagnostics include bounded runtime/release/host/safe-error fields but
   omit owner subject, access token, pipe secret, full document path/content,
   raw LISP and CAD Program bodies.

This extends existing Gateway owner filters. It does not complete a live
two-user/two-device pairing, revoke/WSS close, or local Host-session
invalidation test.

## Validation

Run:

```powershell
python scripts/validate-phase5-release.py
python -m pytest tests/test_phase5_packaging_security.py -q
python -m pytest apps/desktop_agent/tests/test_phase5_safe_diagnostics.py -q
python -m pytest services/gateway/tests/test_phase5_runtime_evidence_isolation.py -q
```

The validator checks:

- exact R22/R23/R24/R25 family set, ranges, frameworks and module paths;
- no unsupported capability claim for an untested family;
- LT never selects Managed Host;
- unsigned lab-only/no-production-publish state;
- separate write/high-risk/advanced-LISP/arbitrary-code switches default off;
- telemetry privacy fields;
- four reviewed XML components with no absolute, remote or traversing path.

## Phase exits and remaining external evidence

| Plan gate | Current result | Missing evidence |
|---|---|---|
| 5.4 release-family packaging | R25 engineering pass; family matrix deferred | CA certificate/timestamp and authorized clean-VM run; older families deferred by operator |
| 5.5 LT certification | Deferred by operator; automated regression retained | Real LT certification is not claimed |
| 5.6 two-user/two-device isolation | Partial, repository evidence | Two authenticated users, two real devices/runtimes, revoke closes WSS and invalidates local Host session |
| 5.7 pilot policy/telemetry | Scaffold only | Signed policy delivery/audit, telemetry ingestion and dashboards, cohort pilot, incident rollback exercise |

Exact lab gaps for the architecture Definition of Done are therefore:

- production certificate/key custody and trusted timestamp evidence;
- malware scan, SBOM, controlled builder provenance and operator approval;
- authorized clean Windows VM install/upgrade/rollback evidence;
- live two-user/two-device/revoke isolation;
- production telemetry and support incident exercise.

Older families and LT are temporarily deferred from the current acceptance
decision, but the compatibility manifest still must not describe them as
supported/certified. R25 write remains closed by release policy until the
remaining production gates are explicitly approved.

## Post-integration Definition of Done review

The create-only CAD Program parser has an exact primitive allowlist, rejects
raw command/LISP, assembly loading and destructive operations, binds preview
to program, runtime, Host version and package digest, and uses transaction
abort for preview.

The original volatile-ledger review finding was fixed before final evidence.
Each successful commit now writes a bounded receipt into a dedicated DWG Named
Objects Dictionary/Xrecord in the same transaction as the entity/layer
effects. A fresh Mechanical 2025 run then saved and reopened the isolated lab
DWG, changed document instance identity, and replayed the old commit. The Host
read the durable receipt first, returned `duplicate`,
`duplicate_of_succeeded_commit=true`, `effect_applied=false`, and retained the
same four entities. Full evidence is recorded in
`phase5-runtime-foundation-evidence.md`.

This closes the R25 POC restart-reconcile finding. Release policy still keeps
`managed_write=false` because a publicly trusted CA signature/timestamp,
authorized clean-VM evidence, two-user/two-device and pilot evidence remain
external blockers. Older-family and LT certification are deferred by operator
direction rather than reported as passed.

The overall architecture DoD status from this review is:

| DoD item reviewed here | Status |
|---|---|
| No arbitrary code/path/assembly/public listener | Pass by bounded registries, local current-user pipe and packaging validation |
| Release family scaffold does not overclaim support | Pass |
| Signed/versioned installer with clean rollback | Partial: R25 Authenticode lab release and exact-hash local rehearsal pass; CA timestamp and clean-VM run pending |
| Older-than-2025 family proven | Deferred by operator; not certified |
| LT 2024+ real smoke | Deferred by operator; not certified |
| Two-user/two-device runtime isolation | Partial: repository evidence only |
| Runtime/release telemetry | Partial: safe field policy and diagnostics, no ingestion/pilot |
| Host-restart duplicate write safety | Pass for the R25 real POC with a DWG-bound atomic receipt |
