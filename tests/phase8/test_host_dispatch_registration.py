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
