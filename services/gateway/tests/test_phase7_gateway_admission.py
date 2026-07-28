from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from hashlib import sha256

import httpx
import jwt
import pytest
import pytest_asyncio
from autocad_contracts import (
    ApprovalDecisionMessage,
    ApprovalRequestMessage,
    ProgramResultMessage,
    approval_decision_proof_payload,
    canonical_capability_hash,
    canonical_package_manifest_hash,
)
from fastmcp import Client
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autocad_gateway.app import GatewayConfig, build_mcp_server, create_app
from autocad_gateway.auth import build_fixture_auth
from autocad_gateway.contracts import (
    CadCommitInput,
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
