"""Run the three bounded Phase 9 reference planners against live R25.

Write effects reuse the signed Phase 8 preview, approval, commit, recovery,
and rollback helpers. This runner does not claim Gateway workflow durability.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from autocad_contracts import (
    canonical_capability_manifest_hash,
    compile_cad_program_v1,
    seal_cad_program_v1,
)
from autocad_desktop_agent.runtime.managed_dotnet import ManagedDotNetCadReadPort
from cad_core.phase9_workflows import (
    PLANNER_REGISTRY_DIGEST,
    TEMPLATE_REGISTRY_DIGEST,
    audit_cleanup,
    plan_auto_dimension_overall,
    render_plate_hole_pattern,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE8_RUNNER = REPO_ROOT / "scripts" / "phase8-live-r25-e2e.py"


def _load_phase8_helpers() -> Any:
    spec = importlib.util.spec_from_file_location(
        "autocad_phase8_live_helpers", PHASE8_RUNNER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("phase8_live_helpers_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P8 = _load_phase8_helpers()


def _revision(snapshot: dict[str, Any]) -> str:
    return str(snapshot["revision"]["revision"])


def _normalize(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": str(entity["handle"]),
        "entity_type": str(entity["type"]).upper(),
        "layer": str(entity["layer"]),
        "space": str(entity.get("space", "model")),
        "bounds": entity.get("bounds"),
        "geometry": entity.get("geometry"),
        "geometry_truncated": bool(entity.get("geometry_truncated", False)),
        "fingerprint": str(entity["fingerprint"]),
    }


async def _detail(port: ManagedDotNetCadReadPort) -> dict[str, Any]:
    value = await port.entity_snapshot()
    if not value.ok or not isinstance(value.payload, dict):
        raise RuntimeError(value.error_code or "detail_snapshot_failed")
    result = dict(value.payload)
    result["entities"] = [_normalize(entity) for entity in result["entities"]]
    return result


async def _summary_count(port: ManagedDotNetCadReadPort) -> int:
    value = await port.drawing_info()
    if not value.ok or not isinstance(value.payload, dict):
        raise RuntimeError(value.error_code or "summary_failed")
    return int(value.payload["entity_count"])


def _context(
    *,
    run_id: str,
    document_id: str,
    revision: str,
) -> dict[str, str]:
    return {
        "run_id": run_id,
        "device_id": "device-phase9-live",
        "source_snapshot_id": f"snapshot-{run_id}-{revision}",
        "document_id": document_id,
        "expected_document_revision": revision,
    }


def _auto_source(
    context: dict[str, str], entities: list[dict[str, Any]]
) -> dict[str, Any]:
    selected = next(
        (
            entity
            for entity in entities
            if entity["entity_type"] == "LWPOLYLINE"
            and not entity["geometry_truncated"]
        ),
        None,
    )
    if selected is None:
        raise RuntimeError("auto_dimension_fixture_missing")
    return plan_auto_dimension_overall(
        context,
        [selected],
        {
            "profile": "mechanical_mm",
            "offset": 10.0,
            "target_layer": "DIM",
        },
    )


async def _live_runtime(
    port: ManagedDotNetCadReadPort,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    handshake = await port._ensure_handshake()
    host_evidence = handshake.get("phase8_host_evidence")
    if not isinstance(host_evidence, dict):
        raise RuntimeError("phase8_host_evidence_missing")
    package_manifest = P8._installed_package_is_signed(
        host_evidence["package_hash"]
    )
    probe = await port.probe()
    capability_manifest_hash = (
        "sha256:" + canonical_capability_manifest_hash(port.manifest(probe))
    )
    return (
        handshake,
        package_manifest,
        P8._pins(host_evidence, capability_manifest_hash),
    )


async def _write_drill(
    port: ManagedDotNetCadReadPort,
    *,
    handshake: dict[str, Any],
    pins: Any,
    run_id: str,
    skill_id: str,
    build_source: Callable[[dict[str, str], list[dict[str, Any]]], dict[str, Any]],
    expected_entity_delta: int,
) -> dict[str, Any]:
    before = await _detail(port)
    before_summary_count = await _summary_count(port)
    revision = _revision(before)
    source = seal_cad_program_v1(
        build_source(
            _context(
                run_id=run_id,
                document_id=before["document_id"],
                revision=revision,
            ),
            before["entities"],
        )
    )
    plan = compile_cad_program_v1(
        source,
        pins,
        compiler_package_hash=P8._file_digest(P8.COMPILER_MODULE),
        materialized_target_refs=[],
    )
    evidence = P8._capability_evidence(
        plan,
        handshake=handshake,
        snapshot={
            **before,
            "returned_count": len(before["entities"]),
        },
        entity_type="ALL",
        run_id=run_id,
    )
    preview = await P8._command(
        port,
        "cad.program.preview",
        {
            "execution_plan": plan.model_dump(mode="json", exclude_none=True),
            "capability_evidence": evidence,
        },
        document_id=before["document_id"],
    )
    after_preview = await _detail(port)
    if (
        _revision(after_preview) != revision
        or await _summary_count(port) != before_summary_count
    ):
        raise RuntimeError(f"{skill_id}_preview_changed_drawing")

    preview_id = f"preview-{run_id}"
    expires_at = P8._timestamp(
        datetime.now(timezone.utc) + timedelta(minutes=30)
    )
    receipt_seed = P8._digest(
        "cad.phase9-live-receipt/1",
        {
            "skill_id": skill_id,
            "execution_plan_digest": plan.execution_plan_digest,
            "preview_digest": preview["result"]["preview_digest"],
        },
    )
    receipt_id = f"AUTOCAD_MCP_PHASE8_{receipt_seed[-32:]}"
    commit = await P8._command(
        port,
        "cad.program.commit",
        {
            "execution_plan": plan.model_dump(mode="json", exclude_none=True),
            "capability_evidence": evidence,
        },
        document_id=before["document_id"],
        approval=P8._approval_factory(
            plan,
            preview_id=preview_id,
            preview_digest=preview["result"]["preview_digest"],
            expires_at=expires_at,
            receipt_id=receipt_id,
            run_id=run_id,
        ),
    )
    after_commit_count = await _summary_count(port)
    if after_commit_count - before_summary_count != expected_entity_delta:
        raise RuntimeError(f"{skill_id}_unexpected_entity_delta")

    receipt_query = await P8._command(
        port,
        "cad.recovery.receipt_query",
        {"receipt_id": receipt_id},
        document_id=before["document_id"],
    )
    checkpoint = commit["result"]["checkpoint"]
    checkpoint_query = await P8._command(
        port,
        "cad.rollback.checkpoint.lookup",
        {"checkpoint_id": checkpoint["id"]},
        document_id=before["document_id"],
    )
    rollback_plan_id = f"rollback-{run_id}"
    rollback_execution_digest = P8._digest(
        "cad.phase9-live-rollback-execution/1",
        {
            "checkpoint_id": checkpoint["id"],
            "checkpoint_digest": checkpoint["digest"],
            "receipt_id": receipt_id,
        },
    )
    rollback_expires_at = P8._timestamp(
        datetime.now(timezone.utc) + timedelta(minutes=30)
    )
    rollback_preview = await P8._command(
        port,
        "cad.rollback.preview",
        {
            "checkpoint_id": checkpoint["id"],
            "checkpoint_digest": checkpoint["digest"],
            "rollback_plan_id": rollback_plan_id,
            "rollback_execution_digest": rollback_execution_digest,
            "expires_at": rollback_expires_at,
        },
        document_id=before["document_id"],
    )
    rollback_result = rollback_preview["result"]
    rollback_receipt_id = rollback_result.get("rollback_receipt_id")
    if not isinstance(rollback_receipt_id, str):
        rollback_receipt_id = (
            "AUTOCAD_MCP_PHASE8_RB_"
            + P8._digest(
                "cad.phase9-live-rollback-receipt/1",
                {
                    "rollback_plan_id": rollback_plan_id,
                    "rollback_plan_digest": rollback_result[
                        "rollback_plan_digest"
                    ],
                },
            )[-32:]
        )
    rollback_commit = await P8._command(
        port,
        "cad.rollback.commit",
        {
            "checkpoint_id": checkpoint["id"],
            "checkpoint_digest": checkpoint["digest"],
            "rollback_plan_id": rollback_plan_id,
            "rollback_plan_digest": rollback_result["rollback_plan_digest"],
            "rollback_execution_digest": rollback_execution_digest,
            "rollback_receipt_id": rollback_receipt_id,
            "expires_at": rollback_expires_at,
        },
        document_id=before["document_id"],
    )
    rollback_validation = await P8._command(
        port,
        "cad.rollback.validate",
        {"rollback_receipt_id": rollback_receipt_id},
        document_id=before["document_id"],
    )
    after_rollback = await _detail(port)
    before_fingerprints = {
        entity["entity_id"]: entity["fingerprint"] for entity in before["entities"]
    }
    after_fingerprints = {
        entity["entity_id"]: entity["fingerprint"]
        for entity in after_rollback["entities"]
    }
    if (
        await _summary_count(port) != before_summary_count
        or before_fingerprints != after_fingerprints
        or rollback_validation["result"].get("valid") is not True
    ):
        raise RuntimeError(f"{skill_id}_rollback_not_restored")
    return {
        "skill_id": skill_id,
        "source_digest": source.semantic_digest,
        "execution_plan_digest": plan.execution_plan_digest,
        "effect_manifest_digest": plan.effect_manifest_digest,
        "required_capabilities": plan.required_capabilities,
        "preview_revision_unchanged": True,
        "entity_count_before": before_summary_count,
        "entity_count_after_commit": after_commit_count,
        "entity_count_after_rollback": before_summary_count,
        "expected_entity_delta": expected_entity_delta,
        "preview": preview,
        "commit": commit,
        "receipt_query": receipt_query,
        "checkpoint_query": checkpoint_query,
        "rollback_preview": rollback_preview,
        "rollback_commit": rollback_commit,
        "rollback_validation": rollback_validation,
    }


async def _run(output: Path) -> None:
    port = ManagedDotNetCadReadPort.from_default_bootstrap(
        agent_version="phase9-live-r25-e2e",
        expected_host_family="R25",
    )
    handshake, package_manifest, pins = await _live_runtime(port)
    host_evidence = handshake["phase8_host_evidence"]
    probe = await port.probe()
    capability_manifest_hash = (
        "sha256:" + canonical_capability_manifest_hash(port.manifest(probe))
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    auto = await _write_drill(
        port,
        handshake=handshake,
        pins=pins,
        run_id=f"auto-{stamp}",
        skill_id="mechanical.auto-dimension-overall",
        build_source=_auto_source,
        expected_entity_delta=2,
    )
    plate = await _write_drill(
        port,
        handshake=handshake,
        pins=pins,
        run_id=f"plate-{stamp}",
        skill_id="mechanical.plate-hole-pattern",
        build_source=lambda context, _entities: render_plate_hole_pattern(
            context,
            {
                "layer": "PHASE9_LIVE",
                "width": 100.0,
                "height": 60.0,
                "hole_diameter": 8.0,
                "rows": 2,
                "columns": 3,
                "margin_x": 10.0,
                "margin_y": 10.0,
                "include_overall_dimensions": True,
            },
        ),
        expected_entity_delta=9,
    )
    cleanup_snapshot = await _detail(port)
    cleanup_revision = _revision(cleanup_snapshot)
    cleanup = audit_cleanup(
        {
            "source_snapshot_id": f"snapshot-cleanup-{stamp}-{cleanup_revision}",
            "document_revision": cleanup_revision,
        },
        cleanup_snapshot["entities"],
        max_candidates=64,
    )
    cleanup_revision_after = _revision(await _detail(port))
    if cleanup_revision_after != cleanup_revision:
        raise RuntimeError("cleanup_audit_changed_drawing")

    result = {
        "schema_version": "cad.phase9-live-r25-e2e/1",
        "created_at": P8._timestamp(datetime.now(timezone.utc)),
        "drawing": "drawing33.dwg",
        "profile": "signed-r25-lab",
        "package_signature": {
            "signed": package_manifest["signed"],
            "lab_only": package_manifest["signing"]["lab_only"],
            "certificate_thumbprint": package_manifest["signing"][
                "certificate_thumbprint"
            ],
            "timestamped": package_manifest["signing"]["timestamped"],
        },
        "host_evidence": host_evidence,
        "capability_manifest_hash": capability_manifest_hash,
        "planner_registry_digest": PLANNER_REGISTRY_DIGEST,
        "template_registry_digest": TEMPLATE_REGISTRY_DIGEST,
        "write_workflows": [auto, plate],
        "cleanup_audit": {
            "report": cleanup,
            "revision_before": cleanup_revision,
            "revision_after": cleanup_revision_after,
        },
        "live_checks": {
            "reference_planners_and_phase8_effect_path": True,
            "preview_approval_commit_validate_rollback": True,
            "cleanup_read_only": True,
            "durable_gateway_workflow_engine": False,
            "gateway_restart_and_agent_reconnect": False,
        },
        "phase9_engineering_go": False,
        "success": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "success": True,
                "output": str(output),
                "package_hash": host_evidence["package_hash"],
                "auto_receipt": auto["commit"]["result"]["receipt_id"],
                "auto_rollback": auto["rollback_commit"]["result"][
                    "rollback_receipt_id"
                ],
                "plate_receipt": plate["commit"]["result"]["receipt_id"],
                "plate_rollback": plate["rollback_commit"]["result"][
                    "rollback_receipt_id"
                ],
                "cleanup_report_digest": cleanup["report_digest"],
                "phase9_engineering_go": False,
            },
            indent=2,
        )
    )


async def _prepare_restart_drill(state_path: Path) -> None:
    port = ManagedDotNetCadReadPort.from_default_bootstrap(
        agent_version="phase9-live-r25-restart-prepare",
        expected_host_family="R25",
    )
    handshake, package_manifest, pins = await _live_runtime(port)
    before = await _detail(port)
    before_count = await _summary_count(port)
    revision = _revision(before)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    run_id = f"restart-auto-{stamp}"
    source = seal_cad_program_v1(
        _auto_source(
            _context(
                run_id=run_id,
                document_id=before["document_id"],
                revision=revision,
            ),
            before["entities"],
        )
    )
    plan = compile_cad_program_v1(
        source,
        pins,
        compiler_package_hash=P8._file_digest(P8.COMPILER_MODULE),
        materialized_target_refs=[],
    )
    evidence = P8._capability_evidence(
        plan,
        handshake=handshake,
        snapshot={**before, "returned_count": len(before["entities"])},
        entity_type="ALL",
        run_id=run_id,
    )
    preview = await P8._command(
        port,
        "cad.program.preview",
        {
            "execution_plan": plan.model_dump(mode="json", exclude_none=True),
            "capability_evidence": evidence,
        },
        document_id=before["document_id"],
    )
    receipt_seed = P8._digest(
        "cad.phase9-live-restart-receipt/1",
        {
            "execution_plan_digest": plan.execution_plan_digest,
            "preview_digest": preview["result"]["preview_digest"],
        },
    )
    receipt_id = f"AUTOCAD_MCP_PHASE8_{receipt_seed[-32:]}"
    expires_at = P8._timestamp(
        datetime.now(timezone.utc) + timedelta(minutes=30)
    )
    commit = await P8._command(
        port,
        "cad.program.commit",
        {
            "execution_plan": plan.model_dump(mode="json", exclude_none=True),
            "capability_evidence": evidence,
        },
        document_id=before["document_id"],
        approval=P8._approval_factory(
            plan,
            preview_id=f"preview-{run_id}",
            preview_digest=preview["result"]["preview_digest"],
            expires_at=expires_at,
            receipt_id=receipt_id,
            run_id=run_id,
        ),
    )
    after_count = await _summary_count(port)
    if after_count != before_count + 2:
        raise RuntimeError("restart_prepare_unexpected_entity_delta")
    state = {
        "schema_version": "cad.phase9-live-r25-restart/1",
        "phase": "committed_waiting_for_save_restart",
        "created_at": P8._timestamp(datetime.now(timezone.utc)),
        "drawing": "drawing33.dwg",
        "package_hash": handshake["phase8_host_evidence"]["package_hash"],
        "package_signature": {
            "signed": package_manifest["signed"],
            "certificate_thumbprint": package_manifest["signing"][
                "certificate_thumbprint"
            ],
        },
        "document_id": before["document_id"],
        "document_revision_before": revision,
        "document_revision_after": commit["result"]["document_revision_after"],
        "entity_count_before": before_count,
        "entity_count_after_commit": after_count,
        "baseline_entity_fingerprints": {
            entity["entity_id"]: entity["fingerprint"]
            for entity in before["entities"]
        },
        "receipt_id": receipt_id,
        "checkpoint_id": commit["result"]["checkpoint"]["id"],
        "checkpoint_digest": commit["result"]["checkpoint"]["digest"],
        "execution_plan_digest": plan.execution_plan_digest,
        "effect_manifest_digest": plan.effect_manifest_digest,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"success": True, "state": str(state_path), **state}, indent=2))


async def _recover_restart_drill(state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        state.get("schema_version") != "cad.phase9-live-r25-restart/1"
        or state.get("phase") != "committed_waiting_for_save_restart"
    ):
        raise RuntimeError("restart_state_invalid")
    port = ManagedDotNetCadReadPort.from_default_bootstrap(
        agent_version="phase9-live-r25-restart-recover",
        expected_host_family="R25",
    )
    handshake, _package_manifest, _pins = await _live_runtime(port)
    if handshake["phase8_host_evidence"]["package_hash"] != state["package_hash"]:
        raise RuntimeError("restart_package_changed")
    current = await _detail(port)
    current_revision = _revision(current)
    if (
        current["document_id"] != state["document_id"]
        or current_revision != state["document_revision_after"]
    ):
        raise RuntimeError("restart_revision_not_restored")
    receipt = await P8._command(
        port,
        "cad.recovery.receipt_query",
        {"receipt_id": state["receipt_id"]},
        document_id=current["document_id"],
    )
    receipt_result = receipt["result"]
    if (
        receipt_result.get("found") is not True
        or receipt_result["checkpoint_id"] != state["checkpoint_id"]
        or receipt_result["checkpoint_digest"] != state["checkpoint_digest"]
    ):
        raise RuntimeError("restart_receipt_not_recovered")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan_id = f"rollback-restart-{stamp}"
    execution_digest = P8._digest(
        "cad.phase9-live-restart-rollback/1",
        {
            "checkpoint_id": state["checkpoint_id"],
            "checkpoint_digest": state["checkpoint_digest"],
            "receipt_id": state["receipt_id"],
        },
    )
    expires_at = P8._timestamp(
        datetime.now(timezone.utc) + timedelta(minutes=30)
    )
    preview = await P8._command(
        port,
        "cad.rollback.preview",
        {
            "checkpoint_id": state["checkpoint_id"],
            "checkpoint_digest": state["checkpoint_digest"],
            "rollback_plan_id": plan_id,
            "rollback_execution_digest": execution_digest,
            "expires_at": expires_at,
        },
        document_id=current["document_id"],
    )
    rollback_receipt_id = (
        "AUTOCAD_MCP_PHASE8_RB_"
        + P8._digest(
            "cad.phase9-live-restart-rollback-receipt/1",
            {
                "rollback_plan_id": plan_id,
                "rollback_plan_digest": preview["result"][
                    "rollback_plan_digest"
                ],
            },
        )[-32:]
    )
    commit = await P8._command(
        port,
        "cad.rollback.commit",
        {
            "checkpoint_id": state["checkpoint_id"],
            "checkpoint_digest": state["checkpoint_digest"],
            "rollback_plan_id": plan_id,
            "rollback_plan_digest": preview["result"]["rollback_plan_digest"],
            "rollback_execution_digest": execution_digest,
            "rollback_receipt_id": rollback_receipt_id,
            "expires_at": expires_at,
        },
        document_id=current["document_id"],
    )
    validation = await P8._command(
        port,
        "cad.rollback.validate",
        {"rollback_receipt_id": rollback_receipt_id},
        document_id=current["document_id"],
    )
    after = await _detail(port)
    after_count = await _summary_count(port)
    after_fingerprints = {
        entity["entity_id"]: entity["fingerprint"]
        for entity in after["entities"]
    }
    if (
        validation["result"].get("valid") is not True
        or after_count != state["entity_count_before"]
        or after_fingerprints != state["baseline_entity_fingerprints"]
    ):
        raise RuntimeError("restart_rollback_not_restored")
    state.update(
        {
            "phase": "recovered_and_rolled_back",
            "recovered_at": P8._timestamp(datetime.now(timezone.utc)),
            "restored_revision_after_restart": current_revision,
            "rollback_receipt_id": rollback_receipt_id,
            "rollback_commit": commit,
            "rollback_validation": validation,
            "entity_count_after_rollback": after_count,
            "baseline_fingerprints_restored": True,
            "success": True,
        }
    )
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "success": True,
                "state": str(state_path),
                "restored_revision": current_revision,
                "rollback_receipt_id": rollback_receipt_id,
                "entity_count_after_rollback": after_count,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "tmp" / "phase9-live-r25-e2e.json",
    )
    parser.add_argument(
        "--restart-drill",
        choices=("prepare", "recover"),
    )
    parser.add_argument(
        "--restart-state",
        type=Path,
        default=REPO_ROOT / "tmp" / "phase9-live-r25-restart.json",
    )
    args = parser.parse_args()
    if args.restart_drill == "prepare":
        asyncio.run(_prepare_restart_drill(args.restart_state.resolve()))
    elif args.restart_drill == "recover":
        asyncio.run(_recover_restart_drill(args.restart_state.resolve()))
    else:
        asyncio.run(_run(args.output.resolve()))


if __name__ == "__main__":
    main()
