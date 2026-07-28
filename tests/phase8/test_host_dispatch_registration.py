from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
R25 = ROOT / "native" / "autocad_managed_host" / "src" / "AutocadMcp.Host.R25"


def test_phase8_pack_is_reachable_from_the_actual_r25_dispatcher():
    pack = R25 / "Phase8ManagedOperationPack.cs"
    callers = []
    for path in R25.glob("*.cs"):
        if path == pack:
            continue
        text = path.read_text(encoding="utf-8")
        if "Phase8ManagedOperationPack." in text:
            callers.append(path)

    assert callers, (
        "Phase8ManagedOperationPack exists but no R25 dispatcher invokes it; "
        "contract-only Host tests are not dispatch evidence"
    )
    dispatcher_text = "\n".join(
        (R25 / name).read_text(encoding="utf-8")
        for name in (
            "ManagedHostExtension.cs",
            "CadProgramHostOperations.cs",
            "AutoCadProgramOperations.cs",
        )
    )
    assert (
        "cad.execution-plan/1" in dispatcher_text
        or "Phase8" in dispatcher_text
    ), "The registered Host command path does not recognize Phase 8 sealed plans"


def test_r25_advertises_exact_phase8_capabilities_and_snapshot_fingerprint():
    read_path = R25 / "AutoCadReadOnlyOperations.cs"
    snapshot_path = R25 / "AutoCadEntitySnapshotOperations.cs"
    read_text = read_path.read_text(encoding="utf-8")
    snapshot_text = snapshot_path.read_text(encoding="utf-8")

    for capability in (
        'AddEntityPack("copy")',
        'AddEntityPack("offset")',
        'AddEntityPack("move")',
        "cad.rollback.checkpoint.v2.",
        "cad.validation.entity.fingerprint.v1",
        "cad.validation.transform.result.v1",
        "cad.validation.rollback.eligibility.v1",
    ):
        assert capability in read_text
    assert "Phase8ManagedOperationPack.EntityFingerprint" in snapshot_text
    assert "blockTable[BlockTableRecord.ModelSpace]" in snapshot_text
    assert 'request.Space is "all" or "paper"' in snapshot_text


def test_managed_phase8_checkpoints_are_routed_through_trusted_recovery():
    operations = (
        R25 / "AutoCadPhase8CanonicalOperations.cs"
    ).read_text(encoding="utf-8")

    for marker in (
        "AUTOCAD_MCP_CHECKPOINT_",
        "AUTOCAD_MCP_RESTORE_V2_",
        "AUTOCAD_MCP_CREATED_RB_",
        "Phase8ManagedOperationPack.Restore",
        "Phase8ManagedOperationPack.RollbackCreatedOutputs",
        "FindPhase8RestoreReceipt",
        "FindPhase8CreatedRollbackReceipt",
    ):
        assert marker in operations
