from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from autocad_contracts import (
    ApprovalDecisionMessage,
    ApprovalRequestMessage,
    ProgramResultMessage,
    ReconcileResultMessage,
    approval_decision_proof_payload,
    canonical_capability_hash,
    canonical_package_manifest_hash,
    rollback_checkpoint_digest,
    rollback_receipt_digest,
)
from fastmcp import Client
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autocad_gateway.app import GatewayConfig, build_mcp_server, create_app
from autocad_gateway.auth import build_fixture_auth
from autocad_gateway.contracts import (
    CadCommitInput,
    CadCommitRollbackInput,
    CadPrepareProgramInput,
    CadPreviewInput,
    CadPreviewRollbackInput,
    Principal,
)
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.identity import owner_key
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.services import GatewayError


OWNER = owner_key("https://issuer.example/", "owner-a")
OTHER = owner_key("https://issuer.example/", "owner-b")
DEVICE = "device-r25"
DOCUMENT = "doc-phase7"
REVISION = "a" * 64
PACKAGE_SHA = "b" * 64
CAPABILITY_SHA = "c" * 64
REGISTRY_SHA = (
    "sha256:5dee5cb2d709f06acff2b8678bb084cd9bfa5d1988e9712510c299d61ba30eb8"
)
WRITE_PRINCIPAL = Principal(
    subject=OWNER, scopes=("autocad.read", "autocad.write")
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, value: dict) -> None:
        self.messages.append(value)

    async def close(self, **_: object) -> None:
        return None


def manifest() -> dict:
    return {
        "schema_version": "cad.capability/1",
        "registry_version": "cad.program/0.2",
        "operation_registry_hash": REGISTRY_SHA,
        "cad_products": [
            {
                "product": "AutoCAD Mechanical",
                "edition": "full",
                "release_year": 2025,
                "series": "R25",
                "runtime": {
                    "id": "managed_dotnet",
                    "role": "primary",
                    "host_family": "R25",
                    "host_version": "0.2.0",
                    "package_id": "autocad.managed_host.r25",
                    "package_version": "0.2.0",
                    "package_hash": "sha256:" + PACKAGE_SHA,
                },
                "capabilities": [
                    "program.preview",
                    "program.commit",
                    "program.validate",
                    "cad.approval.device_local/1",
                    "cad.rollback.checkpoint/1",
                    "cad.rollback.preview/1",
                    "cad.rollback.commit/1",
                ],
            }
        ],
        "fallback_runtimes": [],
    }


CAPABILITIES = (
    "observe",
    "program.preview",
    "program.commit",
    "program.validate",
    "program.ensure_layer",
    "program.create_line",
    "program_preview",
    "program_commit",
    "program_validate",
    "receipt_lookup",
    "checkpoint_lookup",
    "rollback_preview",
    "rollback_commit",
    "rollback_validate",
)


@pytest_asyncio.fixture
async def phase7(tmp_path):
    database = SqliteDatabase(tmp_path / "phase7.db")
    registry = ConnectionRegistry()
    service = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase7_c2",
        request_wait_timeout_seconds=0.01,
        program_enabled=True,
        managed_write_enabled=True,
        allowed_write_device_ids=(DEVICE,),
        program_policy_version="phase7-policy/1",
        phase7_c2_enabled=True,
        trusted_approval_enabled=True,
        device_local_approval_enabled=True,
    )
    await service.initialize()
    device_private_key = Ed25519PrivateKey.generate()
    raw_public_key = device_private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    device_key_fingerprint = sha256(raw_public_key).hexdigest()
    device_public_key = base64.urlsafe_b64encode(raw_public_key).decode(
        "ascii"
    ).rstrip("=")
    await service.repository.seed_device(
        owner_subject=OWNER,
        device_id=DEVICE,
        display_name="Mechanical 2025 C2",
        capabilities=list(CAPABILITIES),
        fixture_auth_ref="ed25519:" + device_key_fingerprint,
    )
    now = datetime.now(timezone.utc).isoformat()
    with service.database.transaction() as conn:
        conn.execute(
            "INSERT INTO device_credentials("
            "device_id, public_key, key_fingerprint, created_at"
            ") VALUES (?, ?, ?, ?)",
            (DEVICE, device_public_key, device_key_fingerprint, now),
        )
    socket = FakeWebSocket()
    connection = AgentConnection(
        device_id=DEVICE,
        session_id="session-phase7",
        websocket=socket,
        protocol_version="cad.agent/2",
        capabilities=CAPABILITIES,
        capability_hash=canonical_capability_hash(CAPABILITIES),
        agent_version="0.7.0",
        package_manifest_hash=canonical_package_manifest_hash(
            [
                {
                    "package_id": "autocad.managed_host.r25",
                    "version": "0.2.0",
                    "sha256": PACKAGE_SHA,
                }
            ]
        ),
        runtime_state="online_idle",
        document_name="drawing33.dwg",
        packages=(
            {
                "package_id": "autocad.managed_host.r25",
                "version": "0.2.0",
                "sha256": PACKAGE_SHA,
            },
        ),
        capability_manifest=manifest(),
        capability_manifest_hash=CAPABILITY_SHA,
        operation_registry_hash=REGISTRY_SHA,
        registry_version="cad.program/0.2",
        write_lock_enabled=True,
        active_document_id=DOCUMENT,
        active_document_revision=REVISION,
    )
    await registry.add(connection)
    await service.on_agent_connected(connection)
    await _seed_snapshot(service)
    try:
        yield service, connection, socket, device_private_key
    finally:
        await service.shutdown()


async def _seed_snapshot(service: DurableGatewayServices) -> None:
    observe = await service.repository.create_job(
        owner_subject=OWNER,
        device_id=DEVICE,
        kind="observe",
        effect_class="read",
        payload={"observation_level": "summary"},
        idempotency_key="source-observe",
        deadline_at=None,
    )
    await service.repository.claim_job(observe["job_id"])
    await service.repository.transition_job(observe["job_id"], "acknowledged")
    snapshot = {
        "snapshot_id": "snapshot-phase7",
        "document_revision": REVISION,
        "observation_level": "summary",
        "drawing": {
            "document_id": DOCUMENT,
            "document_name": "drawing33.dwg",
        },
        "entity_summary": {"entity_count": 0},
        "entities": [],
        "revision_evidence": {
            "revision_schema": "cad.revision/1",
            "revision_strength": "database_object_fingerprint",
            "commit_safe": True,
        },
    }
    await service.repository.finalize_job_result(
        job_id=observe["job_id"],
        device_id=DEVICE,
        command_id=observe["command_id"],
        payload_hash=observe["payload_hash"],
        target="succeeded",
        result={"snapshot": snapshot},
        snapshot=snapshot,
    )


def prepare_request(key: str) -> CadPrepareProgramInput:
    return CadPrepareProgramInput(
        device_id=DEVICE,
        source_snapshot_id="snapshot-phase7",
        operations=[
            {
                "kind": "ensure_layer",
                "operation_id": "layer-1",
                "name": "MCP",
                "color_index": 3,
            },
            {
                "kind": "create_line",
                "operation_id": "line-1",
                "layer": "MCP",
                "start": {"x": 0, "y": 0, "z": 0},
                "end": {"x": 100, "y": 0, "z": 0},
            },
        ],
        postconditions=[{"kind": "entity_count", "expected_created": 1}],
        idempotency_key=key,
    )


async def make_preview(
    service: DurableGatewayServices, *, suffix: str = "one"
) -> dict:
    prepared = await service.program_service.prepare(
        prepare_request("prepare-" + suffix),
        WRITE_PRINCIPAL,
        "prepare-" + suffix,
    )
    pending = await service.program_service.preview(
        CadPreviewInput(
            program_id=prepared.program_id,
            idempotency_key="preview-" + suffix,
        ),
        WRITE_PRINCIPAL,
        "preview-" + suffix,
    )
    job = await service.repository.get_job(OWNER, pending.job_id)
    assert job is not None
    await service.repository.transition_job(job["job_id"], "acknowledged")
    connection = await service.registry.get(DEVICE)
    command = connection.websocket.messages[-1]
    execution = job["payload"]["execution"]
    await service.job_service.handle_message(
        connection,
        ProgramResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=1 if suffix == "one" else 2,
            kind="program_preview",
            status="succeeded",
            payload_hash=job["payload_hash"],
            binding=command["binding"],
            result={
                "preview_id": execution["preview_id"],
                "preview_digest": execution["preview_digest"],
                "expires_at": execution["expires_at"],
                "planned_operation_count": 2,
                "planned_entity_count": 1,
                "planned_layer_count": 1,
                "transaction_aborted": True,
                "drawing_unchanged": True,
            },
        ),
    )
    preview = await service.program_repository.get_preview_by_job(
        OWNER, pending.job_id
    )
    assert preview is not None
    return preview


def job_count(service: DurableGatewayServices, kind: str) -> int:
    with service.database.read_connection() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind = ?", (kind,)
            ).fetchone()[0]
        )


async def signed_local_decision(
    service: DurableGatewayServices,
    connection: AgentConnection,
    consent_id: str,
    private_key: Ed25519PrivateKey,
    *,
    decision: str = "approve",
) -> ApprovalDecisionMessage:
    detail = await service.phase7_admission.portal_consent(OWNER, consent_id)
    request = service.phase7_admission._approval_request(
        detail["intent"], detail["consent"], connection
    )
    decided_at = datetime.now(timezone.utc).isoformat()
    proof_payload = approval_decision_proof_payload(
        approval_request_id=request.approval_request_id,
        approval_request_digest=request.approval_request_digest,
        session_id=request.session_id,
        device_id=request.device_id,
        device_identity_generation=request.device_identity_generation,
        device_key_thumbprint=request.device_key_thumbprint,
        consent_id=request.consent_id,
        intent_id=request.intent_id,
        intent_digest=request.intent_digest,
        challenge_nonce=request.challenge_nonce,
        decision=decision,
        decided_at=decided_at,
    )
    proof = base64.urlsafe_b64encode(
        private_key.sign(proof_payload.encode("utf-8"))
    ).decode("ascii").rstrip("=")
    return ApprovalDecisionMessage(
        session_id=request.session_id,
        device_id=request.device_id,
        correlation_id=request.correlation_id,
        sequence=2,
        issued_at=decided_at,
        approval_request_id=request.approval_request_id,
        approval_request_digest=request.approval_request_digest,
        intent_id=request.intent_id,
        consent_id=request.consent_id,
        intent_digest=request.intent_digest,
        challenge_nonce=request.challenge_nonce,
        decision=decision,
        decided_at=decided_at,
        device_identity_generation=request.device_identity_generation,
        device_key_thumbprint=request.device_key_thumbprint,
        device_session_proof=proof,
    )


async def make_checkpointed_commit(
    service: DurableGatewayServices,
    connection: AgentConnection,
    socket: FakeWebSocket,
    private_key: Ed25519PrivateKey,
) -> dict:
    preview = await make_preview(service, suffix="checkpoint")
    pending = await service.commit_program(
        CadCommitInput(
            preview_id=preview["preview_id"],
            idempotency_key="checkpoint-commit",
        ),
        WRITE_PRINCIPAL,
        "checkpoint-commit",
    )
    decision = await signed_local_decision(
        service, connection, pending.consent_id, private_key
    )
    released = await service.decide_phase7_local_approval(decision)
    job = await service.repository.get_job(OWNER, released["job"]["job_id"])
    assert job is not None
    if job["state"] == "dispatched":
        job = await service.repository.transition_job(job["job_id"], "acknowledged")
    command = next(
        item
        for item in reversed(socket.messages)
        if item.get("kind") == "program_commit"
    )
    execution = job["payload"]["execution"]
    intent = await service.phase7_repository.get_intent(OWNER, pending.intent_id)
    assert intent is not None
    receipt_digest = "sha256:" + "9" * 64
    revision_after = "d" * 64
    gateway_checkpoint = {
        "schema_version": "cad.rollback.checkpoint/1",
        "checkpoint_id": "checkpoint-phase7-e2e",
        "owner_subject": OWNER,
        "original_receipt_id": execution["receipt_id"],
        "original_receipt_digest": receipt_digest,
        "program_id": execution["program_id"],
        "program_revision": execution["program_revision"],
        "program_digest": execution["program_digest"],
        "preview_id": execution["preview_id"],
        "preview_digest": execution["preview_digest"],
        "execution_digest": execution["execution_digest"],
        "document_id": execution["document_id"],
        "document_revision_before": REVISION,
        "document_revision_after": revision_after,
        "created_entities": [
            {
                "handle": "1A",
                "entity_type": "LINE",
                "layer": "MCP",
                "canonical_fingerprint": "sha256:" + "8" * 64,
            }
        ],
        "non_entity_object_created": True,
        "runtime_pins": intent["runtime_pins"],
        "policy_pins": intent["policy_pins"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    checkpoint_digest = rollback_checkpoint_digest(gateway_checkpoint)
    host_checkpoint = {
        **{
            key: value
            for key, value in gateway_checkpoint.items()
            if key not in {"owner_subject", "runtime_pins", "policy_pins"}
        },
        "runtime_and_policy_pins": command["binding"],
        "checkpoint_digest": checkpoint_digest,
    }
    await service.job_service.handle_message(
        connection,
        ProgramResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=10,
            kind="program_commit",
            status="succeeded",
            payload_hash=job["payload_hash"],
            binding=command["binding"],
            result={
                "receipt_id": execution["receipt_id"],
                "receipt_digest": receipt_digest,
                "document_revision_before": REVISION,
                "document_revision_after": revision_after,
                "created_entity_count": 1,
                "rollback_eligible": True,
                "checkpoint_id": host_checkpoint["checkpoint_id"],
                "checkpoint_digest": checkpoint_digest,
                "checkpoint": host_checkpoint,
                "milestone": "effect_and_receipt_committed",
                "duplicate": False,
            },
        ),
    )
    connection.active_document_revision = revision_after
    checkpoint = await service.phase7_repository.get_checkpoint(
        OWNER, host_checkpoint["checkpoint_id"]
    )
    assert checkpoint is not None
    return checkpoint


async def make_released_rollback(
    service: DurableGatewayServices,
    connection: AgentConnection,
    socket: FakeWebSocket,
    private_key: Ed25519PrivateKey,
    *,
    suffix: str,
) -> tuple[dict, dict, dict]:
    checkpoint = await make_checkpointed_commit(
        service, connection, socket, private_key
    )
    service.phase7_admission.policy = service.phase7_admission.policy.__class__(
        **{
            **service.phase7_admission.policy.__dict__,
            "public_rollback_enabled": True,
            "device_local_approval_enabled": False,
            "portal_recent_auth_approval_enabled": True,
        }
    )

    async def preview_provider(checkpoint_value, _request):
        return {
            "current_document_revision": checkpoint_value[
                "document_revision_after"
            ],
            "conflicts": [],
            "runtime_pins": checkpoint_value["runtime_pins"],
            "policy_pins": checkpoint_value["policy_pins"],
        }

    service.phase7_admission.rollback_preview_provider = preview_provider
    preview = await service.phase7_admission.preview_rollback(
        CadPreviewRollbackInput(
            checkpoint_id=checkpoint["checkpoint_id"],
            idempotency_key=f"rollback-preview-{suffix}",
        ),
        WRITE_PRINCIPAL,
        f"rollback-preview-{suffix}",
    )
    pending = await service.phase7_admission.commit_rollback(
        CadCommitRollbackInput(
            rollback_plan_id=preview.rollback_plan_id,
            idempotency_key=f"rollback-commit-{suffix}",
        ),
        WRITE_PRINCIPAL,
        f"rollback-commit-{suffix}",
    )
    detail = await service.phase7_admission.portal_consent(
        OWNER, pending.consent_id
    )
    released = await service.phase7_admission.portal_decide(
        owner_subject=OWNER,
        consent_id=pending.consent_id,
        decision="approved",
        intent_digest=detail["intent"]["intent_digest"],
        consent_version=detail["consent"]["consent_version"],
        nonce=detail["decision_nonce"],
        actor_issuer="https://issuer.example/",
        actor_subject="owner-a",
        auth_time=datetime.now(timezone.utc).timestamp(),
    )
    plan = await service.phase7_repository.get_rollback_plan(
        OWNER, preview.rollback_plan_id
    )
    assert plan is not None
    return checkpoint, plan, released


async def disconnect_started_rollback(
    service: DurableGatewayServices, released: dict
) -> dict:
    job = await service.repository.get_job(OWNER, released["job"]["job_id"])
    assert job is not None
    if job["state"] == "queued":
        await service.job_service.dispatch(job["job_id"], correlation_id=job["job_id"])
        job = await service.repository.get_job(OWNER, job["job_id"])
    if job["state"] == "dispatched":
        job = await service.repository.transition_job(job["job_id"], "acknowledged")
    if job["state"] == "acknowledged":
        job = await service.repository.transition_job(job["job_id"], "running")
    await service.job_service.handle_disconnect(DEVICE)
    unknown = await service.repository.get_job(OWNER, job["job_id"])
    assert unknown is not None and unknown["state"] == "outcome_unknown"
    return unknown


def rollback_commit_result(checkpoint: dict, plan: dict, job: dict) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    revision_after = "e" * 64
    removed_entities = [
        {
            "handle": item["handle"],
            "entity_type": item["entity_type"],
            "prior_fingerprint": item["canonical_fingerprint"],
        }
        for item in checkpoint["created_entities"]
    ]
    gateway_receipt = {
        "schema_version": "cad.rollback.receipt/1",
        "rollback_receipt_id": job["payload"]["arguments"]["rollback_receipt_id"],
        "owner_subject": OWNER,
        "original_receipt_id": checkpoint["original_receipt_id"],
        "original_receipt_digest": checkpoint["original_receipt_digest"],
        "program_digest": checkpoint["program_digest"],
        "original_execution_digest": checkpoint["execution_digest"],
        "original_document_revision": checkpoint["document_revision_before"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "rollback_plan_id": plan["plan_id"],
        "rollback_plan_digest": plan["plan_digest"],
        "rollback_job_id": job["job_id"],
        "rollback_execution_digest": plan["rollback_execution_digest"],
        "document_id": checkpoint["document_id"],
        "document_revision_before": plan["current_document_revision"],
        "document_revision_after": revision_after,
        "removed_entities": removed_entities,
        "runtime_pins": plan["runtime_pins"],
        "policy_pins": plan["policy_pins"],
        "created_at": created_at,
    }
    receipt_digest = rollback_receipt_digest(gateway_receipt)
    runtime = plan["runtime_pins"]
    policy = plan["policy_pins"]
    host_receipt = {
        **{
            key: value
            for key, value in gateway_receipt.items()
            if key
            not in {
                "owner_subject",
                "program_digest",
                "original_execution_digest",
                "original_document_revision",
                "rollback_job_id",
                "runtime_pins",
                "policy_pins",
            }
        },
        "runtime_and_policy_pins": {
            "program_digest": checkpoint["program_digest"],
            "execution_digest": checkpoint["execution_digest"],
            "document_id": checkpoint["document_id"],
            "document_revision": checkpoint["document_revision_before"],
            "runtime_id": runtime["runtime_id"],
            "runtime_role": runtime["runtime_role"],
            "host_family": runtime["host_family"],
            "host_version": runtime["host_version"],
            "package_id": runtime["host_package_id"],
            "package_version": runtime["host_package_version"],
            "package_hash": runtime["host_package_hash"],
            "capability_manifest_hash": policy["capability_manifest_hash"],
            "operation_registry_version": policy["registry_version"],
            "operation_registry_hash": policy["operation_registry_hash"],
            "policy_version": policy["policy_version"],
        },
        "receipt_digest": receipt_digest,
    }
    return {
        "rollback_receipt_id": gateway_receipt["rollback_receipt_id"],
        "receipt_digest": receipt_digest,
        "original_receipt_id": checkpoint["original_receipt_id"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_digest": checkpoint["checkpoint_digest"],
        "rollback_plan_id": plan["plan_id"],
        "rollback_plan_digest": plan["plan_digest"],
        "rollback_execution_digest": plan["rollback_execution_digest"],
        "document_revision_before": plan["current_document_revision"],
        "document_revision_after": revision_after,
        "removed_entity_count": len(removed_entities),
        "receipt": host_receipt,
        "milestone": "effect_and_receipt_committed",
        "duplicate": False,
    }


async def test_all_phase7_flags_default_off_and_direct_lab_is_profile_bounded():
    config = GatewayConfig()
    assert (
        config.phase7_c2_enabled,
        config.trusted_approval_enabled,
        config.device_local_approval_enabled,
        config.portal_recent_auth_approval_enabled,
        config.public_rollback_enabled,
        config.recovery_cases_enabled,
        config.phase6_direct_commit_lab_enabled,
    ) == (False,) * 7
    with pytest.raises(ValueError, match="forbidden outside"):
        GatewayConfig(
            profile="phase7_c2",
            db_path="phase7.db",
            oauth_issuer="https://issuer.example/",
            oauth_audience="https://cad.example/mcp",
            oauth_jwks_uri="https://issuer.example/.well-known/jwks.json",
            public_origin="https://cad.example",
            phase6_direct_commit_lab_enabled=True,
            program_v0_enabled=True,
            managed_write_enabled=True,
            phase6_allowed_device_ids=(DEVICE,),
        ).validate()
    with pytest.raises(ValueError, match="forbids LT write"):
        GatewayConfig(lt_write_enabled=True).validate()


async def test_commit_creates_immutable_intent_and_consent_but_no_job(phase7):
    service, _, _, _ = phase7
    preview = await make_preview(service)
    first = await service.commit_program(
        CadCommitInput(
            preview_id=preview["preview_id"], idempotency_key="commit-one"
        ),
        WRITE_PRINCIPAL,
        "commit-one",
    )
    retry = await service.commit_program(
        CadCommitInput(
            preview_id=preview["preview_id"], idempotency_key="commit-one"
        ),
        WRITE_PRINCIPAL,
        "commit-retry",
    )
    assert first.admission_status == "approval_required"
    assert first.job_id is None and first.receipt_id is None
    assert retry.intent_id == first.intent_id
    assert retry.consent_id == first.consent_id
    assert job_count(service, "program_commit") == 0
    assert await service.phase7_repository.get_intent(
        OTHER, first.intent_id
    ) is None
    assert await service.phase7_repository.get_consent(
        OTHER, first.consent_id
    ) is None


async def test_same_idempotency_with_different_preview_conflicts(phase7):
    service, _, _, _ = phase7
    first = await make_preview(service, suffix="one")
    second = await make_preview(service, suffix="two")
    await service.commit_program(
        CadCommitInput(preview_id=first["preview_id"], idempotency_key="same"),
        WRITE_PRINCIPAL,
        "first",
    )
    with pytest.raises(GatewayError) as conflict:
        await service.commit_program(
            CadCommitInput(
                preview_id=second["preview_id"], idempotency_key="same"
            ),
            WRITE_PRINCIPAL,
            "second",
        )
    assert conflict.value.code == "idempotency_conflict"
    assert job_count(service, "program_commit") == 0


async def test_local_decision_validates_session_nonce_and_releases_once(phase7):
    service, connection, socket, private_key = phase7
    preview = await make_preview(service)
    pending = await service.commit_program(
        CadCommitInput(
            preview_id=preview["preview_id"], idempotency_key="local-approval"
        ),
        WRITE_PRINCIPAL,
        "pending",
    )
    request = ApprovalRequestMessage.model_validate(socket.messages[-1])
    assert request.session_id == connection.session_id
    assert request.intent_id == pending.intent_id
    decision = await signed_local_decision(
        service, connection, pending.consent_id, private_key
    )
    wrong = decision.model_copy(update={"session_id": "replaced-session"})
    with pytest.raises(GatewayError) as replaced:
        await service.decide_phase7_local_approval(wrong)
    assert replaced.value.code == "approval_session_replaced"
    released = await service.decide_phase7_local_approval(decision)
    assert released["job"]["kind"] == "program_commit"
    assert job_count(service, "program_commit") == 1
    with pytest.raises(GatewayError) as replay:
        await service.decide_phase7_local_approval(decision)
    assert replay.value.code == "approval_replay"
    assert job_count(service, "program_commit") == 1


async def test_approval_race_and_runtime_policy_invalidation_are_fail_closed(phase7):
    service, connection, _, private_key = phase7
    preview = await make_preview(service)
    pending = await service.commit_program(
        CadCommitInput(preview_id=preview["preview_id"], idempotency_key="race"),
        WRITE_PRINCIPAL,
        "race",
    )
    decision = await signed_local_decision(
        service, connection, pending.consent_id, private_key
    )
    results = await asyncio.gather(
        service.decide_phase7_local_approval(decision),
        service.decide_phase7_local_approval(decision),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) for item in results) == 1
    assert job_count(service, "program_commit") == 1


async def test_runtime_policy_change_invalidates_unreleased_intent(phase7):
    service, connection, _, private_key = phase7
    second = await make_preview(service, suffix="two")
    pending2 = await service.commit_program(
        CadCommitInput(
            preview_id=second["preview_id"], idempotency_key="stale-policy"
        ),
        WRITE_PRINCIPAL,
        "stale-policy",
    )
    decision2 = await signed_local_decision(
        service, connection, pending2.consent_id, private_key
    )
    connection.operation_registry_hash = "sha256:" + "f" * 64
    with pytest.raises(GatewayError) as stale:
        await service.decide_phase7_local_approval(decision2)
    assert stale.value.code == "binding_mismatch"
    assert job_count(service, "program_commit") == 0
    intent = await service.phase7_repository.get_intent(
        OWNER, pending2.intent_id
    )
    assert intent["state"] == "invalidated"


async def test_portal_recent_auth_expiry_digest_nonce_and_version(phase7):
    service, _, _, _ = phase7
    service.phase7_admission.policy = service.phase7_admission.policy.__class__(
        **{
            **service.phase7_admission.policy.__dict__,
            "device_local_approval_enabled": False,
            "portal_recent_auth_approval_enabled": True,
        }
    )
    preview = await make_preview(service)
    pending = await service.commit_program(
        CadCommitInput(preview_id=preview["preview_id"], idempotency_key="portal"),
        WRITE_PRINCIPAL,
        "portal",
    )
    detail = await service.phase7_admission.portal_consent(
        OWNER, pending.consent_id
    )
    common = {
        "owner_subject": OWNER,
        "consent_id": pending.consent_id,
        "decision": "approved",
        "intent_digest": detail["intent"]["intent_digest"],
        "consent_version": 1,
        "nonce": detail["decision_nonce"],
        "actor_issuer": "https://issuer.example/",
        "actor_subject": "owner-a",
    }
    with pytest.raises(GatewayError) as stale:
        await service.phase7_admission.portal_decide(
            **common, auth_time=0
        )
    assert stale.value.code == "recent_auth_required"
    with pytest.raises(GatewayError) as nonce:
        await service.phase7_admission.portal_decide(
            **{**common, "nonce": "x" * 64},
            auth_time=datetime.now(timezone.utc).timestamp(),
        )
    assert nonce.value.code == "approval_binding_mismatch"
    with pytest.raises(GatewayError) as version:
        await service.phase7_admission.portal_decide(
            **{**common, "consent_version": 2},
            auth_time=datetime.now(timezone.utc).timestamp(),
        )
    assert version.value.code == "approval_binding_mismatch"


async def test_portal_api_enforces_owner_origin_csrf_and_recent_auth(phase7):
    service, _, _, _ = phase7
    service.phase7_admission.policy = service.phase7_admission.policy.__class__(
        **{
            **service.phase7_admission.policy.__dict__,
            "device_local_approval_enabled": False,
            "portal_recent_auth_approval_enabled": True,
        }
    )
    preview = await make_preview(service)
    pending = await service.commit_program(
        CadCommitInput(
            preview_id=preview["preview_id"], idempotency_key="portal-http"
        ),
        WRITE_PRINCIPAL,
        "portal-http",
    )
    detail = await service.phase7_admission.portal_consent(
        OWNER, pending.consent_id
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    issuer = "https://issuer.example/"
    audience = "https://cad.example/mcp"
    auth = build_fixture_auth(
        public_key=public_pem,
        issuer=issuer,
        audience=audience,
        resource_url=audience,
    )
    app = create_app(
        service,
        auth=auth,
        config=GatewayConfig(
            profile="phase7_c2",
            db_path="already-open.db",
            oauth_issuer=issuer,
            oauth_audience=audience,
            oauth_jwks_uri="https://issuer.example/.well-known/jwks.json",
            public_origin="https://cad.example",
            stateless_http=True,
            allowed_hosts=("testserver",),
            program_v0_enabled=True,
            managed_write_enabled=True,
            phase6_allowed_device_ids=(DEVICE,),
            phase7_c2_enabled=True,
            trusted_approval_enabled=True,
            portal_recent_auth_approval_enabled=True,
        ),
    )

    def token(subject: str, *, auth_time: int) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": subject,
                "iss": issuer,
                "aud": audience,
                "iat": now,
                "exp": now + 600,
                "auth_time": auth_time,
                "scope": "autocad.read autocad.write",
            },
            private_pem,
            algorithm="RS256",
        )

    body = {
        "intent_digest": detail["intent"]["intent_digest"],
        "consent_version": 1,
        "challenge_nonce": detail["decision_nonce"],
        "decision": "approve",
    }
    path = f"/api/portal/v1/consents/{pending.consent_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        owned = await client.get(
            path,
            headers={
                "Authorization": f"Bearer {token('owner-a', auth_time=int(time.time()))}"
            },
        )
        hidden = await client.get(
            path,
            headers={
                "Authorization": f"Bearer {token('owner-b', auth_time=int(time.time()))}"
            },
        )
        no_origin = await client.post(
            path + "/approve",
            json=body,
            headers={
                "Authorization": f"Bearer {token('owner-a', auth_time=int(time.time()))}",
                "X-CSRF-Token": body["challenge_nonce"],
            },
        )
        no_csrf = await client.post(
            path + "/approve",
            json=body,
            headers={
                "Authorization": f"Bearer {token('owner-a', auth_time=int(time.time()))}",
                "Origin": "https://cad.example",
            },
        )
        stale = await client.post(
            path + "/approve",
            json=body,
            headers={
                "Authorization": f"Bearer {token('owner-a', auth_time=0)}",
                "Origin": "https://cad.example",
                "X-CSRF-Token": body["challenge_nonce"],
            },
        )
        mismatched_decision = await client.post(
            path + "/deny",
            json=body,
            headers={
                "Authorization": f"Bearer {token('owner-a', auth_time=int(time.time()))}",
                "Origin": "https://cad.example",
                "X-CSRF-Token": body["challenge_nonce"],
            },
        )
        approved = await client.post(
            path + "/approve",
            json=body,
            headers={
                "Authorization": f"Bearer {token('owner-a', auth_time=int(time.time()))}",
                "Origin": "https://cad.example",
                "X-CSRF-Token": body["challenge_nonce"],
            },
        )
    assert owned.status_code == 200
    assert hidden.status_code == 404
    assert no_origin.status_code == 403
    assert no_csrf.status_code == 403
    assert stale.status_code == 401
    assert mismatched_decision.status_code == 409
    assert mismatched_decision.json() == {"error": "approval_binding_mismatch"}
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["intent_id"] == pending.intent_id
    assert job_count(service, "program_commit") == 1


async def test_public_surface_has_rollback_ids_only_and_no_approval_tool(phase7):
    service, _, _, _ = phase7
    server = build_mcp_server(service)
    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resource_templates()
    names = {tool.name for tool in tools}
    assert {"cad_preview_rollback", "cad_commit_rollback"} <= names
    assert not any("approv" in name for name in names)
    rollback = next(tool for tool in tools if tool.name == "cad_preview_rollback")
    encoded = json.dumps(rollback.inputSchema, sort_keys=True)
    assert "entity_handles" not in encoded and "raw_handles" not in encoded
    uris = {str(resource.uriTemplate) for resource in resources}
    assert {
        "cad://intents/{intent_id}",
        "cad://consents/{consent_id}",
        "cad://checkpoints/{checkpoint_id}",
        "cad://rollbacks/{rollback_id}",
        "cad://rollback-receipts/{receipt_id}",
    } <= uris


async def test_phase8_public_tool_snapshot_is_exact_and_primitives_are_denied(phase7):
    service, _, _, _ = phase7
    async with Client(build_mcp_server(service)) as client:
        tools = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in await client.list_tools()
        ]

    def schema_hash(value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    actual = [
        {
            "name": item["name"],
            "annotations": item["annotations"],
            "input_schema_sha256": schema_hash(item["inputSchema"]),
            "output_schema_sha256": schema_hash(item["outputSchema"]),
        }
        for item in tools
    ]
    expected = json.loads(
        (
            Path(__file__).parents[1] / "snapshots" / "phase8_tools.json"
        ).read_text(encoding="utf-8")
    )
    assert actual == expected
    names = {item["name"] for item in actual}
    assert names.isdisjoint(
        {
            "cad_copy",
            "cad_offset",
            "cad_move",
            "cad_rotate",
            "cad_scale",
            "cad_mirror",
            "cad_delete",
            "cad_erase",
            "cad_trim",
            "cad_extend",
            "cad_fillet",
            "cad_chamfer",
            "cad_join",
            "cad_explode",
        }
    )


async def test_rollback_is_off_by_default_and_old_phase6_receipt_is_ineligible(phase7):
    service, _, _, _ = phase7
    with pytest.raises(GatewayError) as disabled:
        await service.phase7_admission.preview_rollback(
            CadPreviewRollbackInput(
                receipt_id="receipt-old",
                idempotency_key="rollback-old",
            ),
            WRITE_PRINCIPAL,
            "rollback",
        )
    assert disabled.value.code == "feature_disabled"
    service.phase7_admission.policy = service.phase7_admission.policy.__class__(
        **{
            **service.phase7_admission.policy.__dict__,
            "public_rollback_enabled": True,
            "portal_recent_auth_approval_enabled": True,
        }
    )
    with pytest.raises(GatewayError) as old:
        await service.phase7_admission.preview_rollback(
            CadPreviewRollbackInput(
                receipt_id="receipt-old",
                idempotency_key="rollback-old",
            ),
            WRITE_PRINCIPAL,
            "rollback",
        )
    assert old.value.code == "rollback_unavailable"


async def test_rollback_preview_recent_auth_approval_consumes_consent_and_releases_job(
    phase7,
):
    service, connection, socket, private_key = phase7
    checkpoint = await make_checkpointed_commit(
        service, connection, socket, private_key
    )
    service.phase7_admission.policy = service.phase7_admission.policy.__class__(
        **{
            **service.phase7_admission.policy.__dict__,
            "public_rollback_enabled": True,
            "device_local_approval_enabled": False,
            "portal_recent_auth_approval_enabled": True,
        }
    )

    async def preview_provider(checkpoint_value, _request):
        return {
            "current_document_revision": checkpoint_value[
                "document_revision_after"
            ],
            "conflicts": [],
            "runtime_pins": checkpoint_value["runtime_pins"],
            "policy_pins": checkpoint_value["policy_pins"],
        }

    service.phase7_admission.rollback_preview_provider = preview_provider
    plan = await service.phase7_admission.preview_rollback(
        CadPreviewRollbackInput(
            checkpoint_id=checkpoint["checkpoint_id"],
            idempotency_key="rollback-preview-e2e",
        ),
        WRITE_PRINCIPAL,
        "rollback-preview-e2e",
    )
    pending = await service.phase7_admission.commit_rollback(
        CadCommitRollbackInput(
            rollback_plan_id=plan.rollback_plan_id,
            idempotency_key="rollback-commit-e2e",
        ),
        WRITE_PRINCIPAL,
        "rollback-commit-e2e",
    )
    assert pending.state == "awaiting_approval"
    detail = await service.phase7_admission.portal_consent(
        OWNER, pending.consent_id
    )
    released = await service.phase7_admission.portal_decide(
        owner_subject=OWNER,
        consent_id=pending.consent_id,
        decision="approved",
        intent_digest=detail["intent"]["intent_digest"],
        consent_version=detail["consent"]["consent_version"],
        nonce=detail["decision_nonce"],
        actor_issuer="https://issuer.example/",
        actor_subject="owner-a",
        auth_time=datetime.now(timezone.utc).timestamp(),
    )

    assert released["job"]["kind"] == "rollback_commit"
    assert released["job"]["payload"]["intent_id"] == pending.intent_id
    assert (
        released["job"]["payload"]["intent_digest"]
        == detail["intent"]["intent_digest"]
    )
    consumed = await service.phase7_repository.get_consent(
        OWNER, pending.consent_id
    )
    intent = await service.phase7_repository.get_intent(OWNER, pending.intent_id)
    assert consumed["state"] == "consumed"
    assert intent["state"] == "released"
    assert intent["released_job_id"] == released["job"]["job_id"]


async def test_rollback_reconcile_unknown_preserves_lock_and_mismatch_fails_closed(
    phase7,
):
    service, connection, socket, private_key = phase7
    _, _, released = await make_released_rollback(
        service, connection, socket, private_key, suffix="unknown"
    )
    unknown = await disconnect_started_rollback(service, released)
    binding = unknown["payload"]["binding"]

    await service.job_service.handle_reconcile_result(
        connection,
        ReconcileResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=unknown["job_id"],
            command_id=unknown["command_id"],
            sequence=20,
            status="terminal",
            payload_hash=unknown["payload_hash"],
            result_status="failed",
            error_code="outcome_unknown",
            error_message="Rollback outcome remains unknown",
            kind="rollback_commit",
            binding=binding,
        ),
    )
    still_unknown = await service.repository.get_job(OWNER, unknown["job_id"])
    assert still_unknown["state"] == "outcome_unknown"
    with service.database.read_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM cad_program_write_locks WHERE job_id = ?",
            (unknown["job_id"],),
        ).fetchone()[0] == 1

    service.phase7_recovery.cases_enabled = True
    mismatched = dict(binding)
    mismatched["document_revision"] = "f" * 64
    await service.job_service.handle_reconcile_result(
        connection,
        ReconcileResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=unknown["job_id"],
            command_id=unknown["command_id"],
            sequence=21,
            status="terminal",
            payload_hash=unknown["payload_hash"],
            result_status="succeeded",
            result={},
            kind="rollback_commit",
            binding=mismatched,
        ),
    )
    attention = await service.repository.get_job(OWNER, unknown["job_id"])
    assert attention["state"] == "needs_attention"
    assert await service.phase7_repository.list_rollback_receipts(OWNER) == []
    with service.database.read_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM cad_program_write_locks WHERE job_id = ?",
            (unknown["job_id"],),
        ).fetchone()[0] == 1


async def test_rollback_reconcile_exact_host_commit_materializes_once(phase7):
    service, connection, socket, private_key = phase7
    checkpoint, plan, released = await make_released_rollback(
        service, connection, socket, private_key, suffix="committed"
    )
    unknown = await disconnect_started_rollback(service, released)
    result = rollback_commit_result(checkpoint, plan, unknown)

    for sequence in (30, 31):
        await service.job_service.handle_reconcile_result(
            connection,
            ReconcileResultMessage(
                session_id=connection.session_id,
                device_id=DEVICE,
                job_id=unknown["job_id"],
                command_id=unknown["command_id"],
                sequence=sequence,
                status="terminal",
                payload_hash=unknown["payload_hash"],
                result_status="succeeded",
                result=result,
                kind="rollback_commit",
                binding=unknown["payload"]["binding"],
            ),
        )

    reconciled = await service.repository.get_job(OWNER, unknown["job_id"])
    receipts = await service.phase7_repository.list_rollback_receipts(OWNER)
    evidence = await service.phase7_repository.list_evidence(
        OWNER, unknown["job_id"]
    )
    assert reconciled["state"] == "succeeded"
    assert len(receipts) == 1
    assert receipts[0]["receipt_digest"] == result["receipt_digest"]
    assert [
        item["payload"]["milestone"] for item in evidence
    ].count("terminal_persisted") == 1
    with service.database.read_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM cad_program_write_locks WHERE job_id = ?",
            (unknown["job_id"],),
        ).fetchone()[0] == 0


def test_phase6_lab_output_remains_compatible():
    fields = set(
        __import__(
            "autocad_gateway.contracts", fromlist=["CadCommitOutput"]
        ).CadCommitOutput.model_fields
    )
    assert {
        "receipt_id",
        "job_id",
        "state",
        "program_digest",
        "execution_digest",
        "binding_digest",
        "document_revision_before",
        "document_revision_after",
        "effect_summary",
        "duplicate",
        "job_uri",
        "resource_uri",
    } <= fields


async def test_phase6_direct_commit_requires_explicit_lab_flag():
    class ProgramStub:
        async def commit(self, request, principal, correlation_id):
            return request, principal, correlation_id

    service = object.__new__(DurableGatewayServices)
    service.phase7_admission = None
    service.program_service = ProgramStub()
    service.phase6_direct_commit_lab_enabled = False
    request = CadCommitInput(
        preview_id="preview-lab", idempotency_key="lab-commit"
    )
    with pytest.raises(GatewayError) as disabled:
        await service.commit_program(request, WRITE_PRINCIPAL, "lab")
    assert disabled.value.code == "feature_disabled"
    service.phase6_direct_commit_lab_enabled = True
    result = await service.commit_program(request, WRITE_PRINCIPAL, "lab")
    assert result == (request, WRITE_PRINCIPAL, "lab")
