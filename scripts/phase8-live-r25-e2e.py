"""Run bounded signed-lab Phase 8 create/transform rollback drills on R25.

This runner talks directly to the current-user Managed Host pipe. It never
prints or persists the pipe bootstrap secret. The active drawing is expected
to be a disposable test drawing.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from autocad_contracts import (
    ExecutionPins,
    build_execution_binding_v1,
    canonical_capability_manifest_hash,
    canonical_json,
    canonical_phase8_capability_evidence_digest,
    compile_cad_program_v1,
    seal_cad_program_v1,
)
from autocad_desktop_agent.runtime.managed_dotnet import (
    ManagedDotNetCadReadPort,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_FIXTURE = (
    REPO_ROOT
    / "packages"
    / "contracts"
    / "fixtures"
    / "cad-program-1.0-phase8-target-vector.json"
)
COMPILER_MODULE = (
    REPO_ROOT
    / "packages"
    / "contracts"
    / "src"
    / "autocad_contracts"
    / "phase8_contracts.py"
)
INSTALLED_MANIFEST = (
    Path(os.environ["APPDATA"])
    / "Autodesk"
    / "ApplicationPlugins"
    / "AutocadMcp.ManagedHost.R25.bundle"
    / "Contents"
    / "Shared"
    / "package-manifest.json"
)


def _digest(domain: str, value: Any) -> str:
    payload = canonical_json({"domain": domain, "value": value}).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _timestamp(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    return (
        utc.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{utc.microsecond:06d}0+00:00"
    )


def _revision(snapshot: dict[str, Any]) -> str:
    value = snapshot.get("revision")
    if not isinstance(value, dict) or not isinstance(value.get("revision"), int):
        raise RuntimeError("snapshot_revision_missing")
    return str(value["revision"])


def _snapshot_id(run_id: str, revision: str) -> str:
    return f"snapshot-{run_id}-{revision}"


def _entity(snapshot: dict[str, Any], entity_type: str) -> dict[str, Any]:
    for value in snapshot.get("entities", []):
        if value.get("type") == entity_type and value.get("space") == "model":
            return value
    raise RuntimeError(f"no_{entity_type.lower()}_in_model_space")


def _installed_package_is_signed(package_hash: str) -> dict[str, Any]:
    value = json.loads(INSTALLED_MANIFEST.read_text(encoding="utf-8"))
    signing = value.get("signing")
    if (
        value.get("signed") is not True
        or value.get("package_hash") != package_hash
        or not isinstance(signing, dict)
        or signing.get("authenticode") is not True
        or not signing.get("certificate_thumbprint")
    ):
        raise RuntimeError("installed_package_signature_evidence_invalid")
    return value


def _source(
    fixture: dict[str, Any],
    *,
    kind: str,
    run_id: str,
    document_id: str,
    revision: str,
    snapshot_id: str,
) -> dict[str, Any]:
    source = deepcopy(fixture["source"])
    source.pop("semantic_digest", None)
    source["program_id"] = f"phase8-live-{kind}-{run_id}"
    source["program_revision"] = 1
    source["device_id"] = "device-phase8-live"
    source["source_snapshot_id"] = snapshot_id
    source["document_id"] = document_id
    source["expected_document_revision"] = revision
    source["operations"] = [
        deepcopy(
            next(
                item
                for item in fixture["source"]["operations"]
                if item["kind"] == kind
            )
        )
    ]
    operation = source["operations"][0]
    operation["operation_id"] = f"{kind.removesuffix('_entity')}-{run_id}"
    operation["target_ref_id"] = "ref-target"
    if kind == "move_entity":
        operation["displacement"] = {
            "x": {
                "op": "literal",
                "value": {"type": "length", "value": "1", "unit": "mm"},
            },
            "y": {
                "op": "literal",
                "value": {"type": "length", "value": "0", "unit": "mm"},
            },
            "z": {
                "op": "literal",
                "value": {"type": "length", "value": "0", "unit": "mm"},
            },
        }
    source["required_capabilities"] = ["cad.program.v1.compile"]
    return source


def _pins(
    host_evidence: dict[str, Any],
    capability_manifest_hash: str,
) -> ExecutionPins:
    rollout = {
        "profile": "phase8-signed-r25-lab",
        "source": True,
        "create_pack": True,
        "transform_pack": True,
        "checkpoint_v2": True,
        "topology_pack": False,
        "delete_pack": False,
    }
    return ExecutionPins.model_validate(
        {
            "runtime_id": host_evidence["runtime_id"],
            "runtime_role": host_evidence["runtime_role"],
            "host_family": host_evidence["host_family"],
            "host_version": host_evidence["host_version"],
            "package_id": host_evidence["package_id"],
            "package_version": host_evidence["package_version"],
            "package_hash": host_evidence["package_hash"],
            "capability_manifest_hash": capability_manifest_hash,
            "operation_registry_version": host_evidence[
                "operation_registry_version"
            ],
            "operation_registry_hash": host_evidence[
                "operation_registry_hash"
            ],
            "policy_version": "phase8-signed-r25-lab.1",
            "rollout_policy_digest": _digest(
                "cad.rollout-policy/1",
                rollout,
            ),
        }
    )


def _capability_evidence(
    plan: Any,
    *,
    handshake: dict[str, Any],
    snapshot: dict[str, Any],
    entity_type: str,
    run_id: str,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    host = handshake["phase8_host_evidence"]
    agent_digest = _digest(
        "cad.live-agent-evidence/1",
        {
            "host_evidence_digest": host["host_evidence_digest"],
            "document_id": snapshot["document_id"],
            "document_revision": _revision(snapshot),
            "entity_count": snapshot["returned_count"],
        },
    )
    result = []
    for index, capability in enumerate(plan.required_capabilities, start=1):
        if capability == "cad.program.v1.compile":
            operation_pack = "compiler.core/1"
            claim_entity_type = "ALL"
        elif ".move." in capability:
            operation_pack = "transform.exact/1"
            claim_entity_type = entity_type
        else:
            operation_pack = "create-equivalent/1"
            claim_entity_type = entity_type
        value = {
            "schema_version": "cad.capability-evidence/1",
            "evidence_id": f"live-{run_id}-{index}",
            "evidence_authority": "gateway_server",
            "device_id": plan.device_id,
            "capability_key": capability,
            "operation_pack": operation_pack,
            "runtime_id": plan.execution_pins.runtime_id,
            "host_family": plan.execution_pins.host_family,
            "entity_type": claim_entity_type,
            "support_state": handshake["capability_states"][capability],
            "package_hash": plan.execution_pins.package_hash,
            "capability_manifest_hash": (
                plan.execution_pins.capability_manifest_hash
            ),
            "operation_registry_hash": (
                plan.execution_pins.operation_registry_hash
            ),
            "package_signature_verified": True,
            "agent_evidence_digest": agent_digest,
            "host_evidence_digest": host["host_evidence_digest"],
            "cohort": "phase8-signed-r25-lab",
            "evidence_version": "phase8.evidence.1",
            "issued_at": _timestamp(now - timedelta(minutes=1)),
            "valid_until": _timestamp(now + timedelta(hours=1)),
        }
        value["evidence_digest"] = canonical_phase8_capability_evidence_digest(
            value
        )
        result.append(value)
    return result


async def _command(
    port: ManagedDotNetCadReadPort,
    operation_id: str,
    arguments: dict[str, Any],
    *,
    document_id: str,
    approval: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "operation_id": operation_id,
        "operation_version": 1,
        "arguments": arguments,
        "document_id": document_id,
    }
    envelope = port._envelope("command", payload)
    if approval is not None:
        arguments["approval_binding"] = approval(envelope["command_id"])
        envelope["payload_hash"] = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
    response = await port._transport.request(envelope)
    if response.get("message_type") == "error":
        raise RuntimeError(
            canonical_json(response.get("payload", {"error_code": "internal_error"}))
        )
    value = port._validate_response(response, expected_type="result")
    if value.get("operation_id") != operation_id:
        raise RuntimeError("protocol_mismatch")
    result = value.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("protocol_mismatch")
    return {
        "command_id": envelope["command_id"],
        "status": value.get("status"),
        "result": result,
        "runtime_evidence": value.get("runtime_evidence"),
    }


def _approval_factory(
    plan: Any,
    *,
    preview_id: str,
    preview_digest: str,
    expires_at: str,
    receipt_id: str,
    run_id: str,
) -> Callable[[str], dict[str, Any]]:
    binding = build_execution_binding_v1(
        plan,
        action="commit",
        preview_id=preview_id,
        preview_expires_at=expires_at,
        receipt_id=receipt_id,
    )
    intent_digest = _digest(
        "cad.phase8-live-operator-intent/1",
        {
            "scope": "drawing33-phase8-lab",
            "execution_plan_digest": plan.execution_plan_digest,
            "preview_id": preview_id,
            "receipt_id": receipt_id,
        },
    )
    approval_digest = _digest(
        "cad.phase8-live-approval-proof/1",
        {
            "intent_digest": intent_digest,
            "execution_plan_digest": plan.execution_plan_digest,
            "preview_digest": preview_digest,
            "receipt_id": receipt_id,
        },
    )

    def build(command_id: str) -> dict[str, Any]:
        return {
            "schema_version": "cad.phase8-approval-binding/1",
            "action": "program_commit",
            "intent_id": f"intent-{run_id}",
            "consent_id": f"consent-{run_id}",
            "intent_digest": intent_digest,
            "approval_proof_digest": approval_digest,
            "device_id": plan.device_id,
            "document_id": plan.document_id,
            "document_revision": plan.expected_document_revision,
            "job_id": f"job-{run_id}",
            "command_id": command_id,
            "idempotency_key": f"idempotency-{run_id}",
            "source_digest": plan.source_digest,
            "execution_plan_digest": plan.execution_plan_digest,
            "execution_binding_digest": binding.execution_binding_digest,
            "expansion_digest": plan.expansion_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "validation_profiles_digest": plan.validation_profiles_digest,
            "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "preview_id": preview_id,
            "preview_digest": preview_digest,
            "preview_expires_at": expires_at,
            "receipt_id": receipt_id,
        }

    return build


async def _snapshot(
    port: ManagedDotNetCadReadPort,
    document_id: str | None = None,
) -> dict[str, Any]:
    return await port._command(
        "entity.snapshot.page",
        arguments={
            "limit": 200,
            "space": "model",
            "types": ["LINE", "CIRCLE", "LWPOLYLINE"],
        },
    )


async def _drill(
    port: ManagedDotNetCadReadPort,
    *,
    fixture: dict[str, Any],
    handshake: dict[str, Any],
    pins: ExecutionPins,
    kind: str,
    entity_type: str,
    run_id: str,
) -> dict[str, Any]:
    before = await _snapshot(port, handshake["active_document_id"])
    revision = _revision(before)
    snapshot_id = _snapshot_id(run_id, revision)
    target = _entity(before, entity_type)
    source = seal_cad_program_v1(
        _source(
            fixture,
            kind=kind,
            run_id=run_id,
            document_id=before["document_id"],
            revision=revision,
            snapshot_id=snapshot_id,
        )
    )
    plan = compile_cad_program_v1(
        source,
        pins,
        compiler_package_hash=_file_digest(COMPILER_MODULE),
        materialized_target_refs=[
            {
                "ref_id": "ref-target",
                "owner_id": "owner-phase8-live",
                "device_id": source.device_id,
                "document_id": before["document_id"],
                "snapshot_id": snapshot_id,
                "document_revision": revision,
                "entity_id": target["handle"],
                "entity_type": entity_type,
                "fingerprint": target["fingerprint"],
            }
        ],
        materialized_owner_id="owner-phase8-live",
    )
    evidence = _capability_evidence(
        plan,
        handshake=handshake,
        snapshot=before,
        entity_type=entity_type,
        run_id=run_id,
    )
    preview_id = f"preview-{run_id}"
    expires_at = _timestamp(datetime.now(timezone.utc) + timedelta(minutes=30))
    preview = await _command(
        port,
        "cad.program.preview",
        {
            "execution_plan": plan.model_dump(mode="json", exclude_none=True),
            "capability_evidence": evidence,
        },
        document_id=before["document_id"],
    )
    preview_result = preview["result"]
    receipt_seed = _digest(
        "cad.phase8-live-receipt/1",
        {
            "execution_plan_digest": plan.execution_plan_digest,
            "preview_digest": preview_result["preview_digest"],
        },
    )
    receipt_id = f"AUTOCAD_MCP_PHASE8_{receipt_seed[-32:]}"
    commit = await _command(
        port,
        "cad.program.commit",
        {
            "execution_plan": plan.model_dump(mode="json", exclude_none=True),
            "capability_evidence": evidence,
        },
        document_id=before["document_id"],
        approval=_approval_factory(
            plan,
            preview_id=preview_id,
            preview_digest=preview_result["preview_digest"],
            expires_at=expires_at,
            receipt_id=receipt_id,
            run_id=run_id,
        ),
    )
    commit_result = commit["result"]
    after_commit = await _snapshot(port, before["document_id"])
    receipt_query = await _command(
        port,
        "cad.recovery.receipt_query",
        {"receipt_id": receipt_id},
        document_id=before["document_id"],
    )
    checkpoint = commit_result["checkpoint"]
    checkpoint_query = await _command(
        port,
        "cad.rollback.checkpoint.lookup",
        {"checkpoint_id": checkpoint["id"]},
        document_id=before["document_id"],
    )
    rollback_plan_id = f"rollback-{run_id}"
    rollback_execution_digest = _digest(
        "cad.phase8-live-rollback-execution/1",
        {
            "checkpoint_id": checkpoint["id"],
            "checkpoint_digest": checkpoint["digest"],
            "receipt_id": receipt_id,
        },
    )
    rollback_expires_at = _timestamp(
        datetime.now(timezone.utc) + timedelta(minutes=30)
    )
    rollback_preview = await _command(
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
    rollback_preview_result = rollback_preview["result"]
    rollback_commit = await _command(
        port,
        "cad.rollback.commit",
        {
            "checkpoint_id": checkpoint["id"],
            "checkpoint_digest": checkpoint["digest"],
            "rollback_plan_id": rollback_plan_id,
            "rollback_plan_digest": rollback_preview_result[
                "rollback_plan_digest"
            ],
            "rollback_execution_digest": rollback_execution_digest,
            "rollback_receipt_id": rollback_preview_result[
                "rollback_receipt_id"
            ],
            "expires_at": rollback_expires_at,
        },
        document_id=before["document_id"],
    )
    rollback_validation = await _command(
        port,
        "cad.rollback.validate",
        {
            "rollback_receipt_id": rollback_preview_result[
                "rollback_receipt_id"
            ]
        },
        document_id=before["document_id"],
    )
    after_rollback = await _snapshot(port, before["document_id"])
    restored_target = next(
        item
        for item in after_rollback["entities"]
        if item["handle"] == target["handle"]
    )
    if restored_target["fingerprint"] != target["fingerprint"]:
        raise RuntimeError(f"{kind}_target_not_restored")
    if before["returned_count"] != after_rollback["returned_count"]:
        raise RuntimeError(f"{kind}_entity_count_not_restored")
    if rollback_validation["result"].get("valid") is not True:
        raise RuntimeError(f"{kind}_rollback_validation_failed")
    return {
        "kind": kind,
        "entity_type": entity_type,
        "target_handle": target["handle"],
        "target_fingerprint_before": target["fingerprint"],
        "execution_plan_digest": plan.execution_plan_digest,
        "effect_manifest_digest": plan.effect_manifest_digest,
        "required_capabilities": plan.required_capabilities,
        "preview": preview,
        "commit": commit,
        "after_commit_revision": _revision(after_commit),
        "receipt_query": receipt_query,
        "checkpoint_query": checkpoint_query,
        "rollback_preview": rollback_preview,
        "rollback_commit": rollback_commit,
        "rollback_validation": rollback_validation,
        "after_rollback_revision": _revision(after_rollback),
        "target_fingerprint_after_rollback": restored_target["fingerprint"],
        "entity_count_before": before["returned_count"],
        "entity_count_after_rollback": after_rollback["returned_count"],
    }


async def _run(output: Path) -> None:
    port = ManagedDotNetCadReadPort.from_default_bootstrap(
        agent_version="phase8-live-r25-e2e",
        expected_host_family="R25",
    )
    handshake = await port._ensure_handshake()
    host_evidence = handshake.get("phase8_host_evidence")
    if not isinstance(host_evidence, dict):
        raise RuntimeError("phase8_host_evidence_missing")
    package_manifest = _installed_package_is_signed(
        host_evidence["package_hash"]
    )
    probe = await port.probe()
    manifest = port.manifest(probe)
    capability_manifest_hash = (
        f"sha256:{canonical_capability_manifest_hash(manifest)}"
    )
    pins = _pins(host_evidence, capability_manifest_hash)
    fixture = json.loads(TARGET_FIXTURE.read_text(encoding="utf-8"))
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    copy = await _drill(
        port,
        fixture=fixture,
        handshake=handshake,
        pins=pins,
        kind="copy_entity",
        entity_type="LINE",
        run_id=f"copy-{run_stamp}",
    )
    move = await _drill(
        port,
        fixture=fixture,
        handshake=handshake,
        pins=pins,
        kind="move_entity",
        entity_type="CIRCLE",
        run_id=f"move-{run_stamp}",
    )
    result = {
        "schema_version": "cad.phase8-live-r25-e2e/1",
        "created_at": _timestamp(datetime.now(timezone.utc)),
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
        "drills": [copy, move],
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
                "copy_receipt": copy["commit"]["result"]["receipt_id"],
                "copy_rollback": copy["rollback_commit"]["result"][
                    "rollback_receipt_id"
                ],
                "move_receipt": move["commit"]["result"]["receipt_id"],
                "move_rollback": move["rollback_commit"]["result"][
                    "rollback_receipt_id"
                ],
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "tmp" / "phase8-live-r25-e2e.json",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.output.resolve()))


if __name__ == "__main__":
    main()
