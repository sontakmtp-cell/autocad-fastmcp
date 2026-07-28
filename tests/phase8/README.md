# Phase 8 cross-stack conformance

This directory is an independent conformance suite. It executes the real
`autocad_contracts` compiler against the checked-in Python/C# golden vector,
stores that exact sealed result through the real Gateway repository, verifies
the Phase 7 public MCP surface plus the bounded Phase 8
`cad_prepare_program` schema delta, and exercises adversarial source/plan
inputs.

The suite deliberately distinguishes green gates from failing integration
gates:

- LT negatives, profile snapshots, materialized/capability digest
  recomputation, Desktop admission and Host checkpoint-v2 contracts are green;
- the compiler 1.1 output does not match the checked-in 1.0 cross-runtime
  golden, so compiler/Gateway/wire gates fail;
- an unbound Phase 8-shaped Phase 7 release is currently accepted and the R25
  dispatcher does not invoke `Phase8ManagedOperationPack`; both are failing
  security gates;
- `cad.agent/2` and Desktop admission expose the shared `CadExecutionPlanV1`,
  binding and capability-evidence models, but wire acceptance remains red until
  the canonical fixture mismatch is resolved;
- Mechanical 2025 commit/rollback remains live evidence and cannot be replaced
  by these tests.

Run:

```powershell
python scripts\test-phase8-conformance.py
.\scripts\test-phase8-regression.ps1 -ListOnly
```

The first command is the canonical single-command check. It invokes pytest
through `uv --project services/gateway`, then the Managed Host Core Phase 8
tests. The Host contract project does not require Autodesk references. Use
`--python-only` when diagnosing Python independently.

The regression runner is intentionally opt-in per suite. It never changes
feature flags, launches AutoCAD, or treats ezdxf as live DWG evidence. The
R25 bundle is local-only:

```powershell
.\scripts\build-phase8-r25-host.ps1
```

GitHub Actions does not build the Desktop Agent executable or require Autodesk
assemblies.
