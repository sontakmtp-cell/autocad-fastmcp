from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
import pytest_asyncio
import httpx
import jwt
from autocad_contracts import (
    HeartbeatMessage,
    ProgramCommandMessage,
    ProgramResultMessage,
    ReconcileResultMessage,
    canonical_capability_hash,
    program_command_payload_hash,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp import Client

from autocad_gateway.app import GatewayConfig, build_mcp_server, create_app
from autocad_gateway.auth import build_fixture_auth
from autocad_gateway.contracts import (
    CadCommitInput,
    CadPrepareProgramInput,
    CadPreviewInput,
    CadValidateInput,
    Principal,
)
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict
from autocad_gateway.identity import owner_key
from autocad_gateway.services import GatewayError


OWNER = owner_key("https://issuer.example/", "owner-a")
OTHER = owner_key("https://issuer.example/", "owner-b")
DEVICE = "device-r25"
DOCUMENT = "doc-phase6"
REVISION = "a" * 64
PACKAGE_SHA = "b" * 64
CAPABILITY_SHA = "c" * 64
REGISTRY_SHA = (
    "sha256:5dee5cb2d709f06acff2b8678bb084cd9bfa5d1988e9712510c299d61ba30eb8"
)
WRITE_PRINCIPAL = Principal(
    subject=OWNER, scopes=("autocad.read", "autocad.write")
)
READ_PRINCIPAL = Principal(subject=OWNER, scopes=("autocad.read",))
SNAPSHOTS = Path(__file__).parents[1] / "snapshots"


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, value: dict) -> None:
        self.messages.append(value)

    async def close(self, **_: object) -> None:
        return None


def _manifest(*, registry_version: str = "cad.program/0.2") -> dict:
    return {
        "schema_version": "cad.capability/1",
        "registry_version": registry_version,
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
async def phase6(tmp_path):
    database = SqliteDatabase(tmp_path / "phase6.db")
    registry = ConnectionRegistry()
    service = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase6_program",
        request_wait_timeout_seconds=0.01,
        program_enabled=True,
        managed_write_enabled=True,
        allowed_write_device_ids=(DEVICE,),
        program_policy_version="phase6-policy/1",
    )
    await service.initialize()
    await service.repository.seed_device(
        owner_subject=OWNER,
        device_id=DEVICE,
        display_name="Mechanical 2025 lab",
        capabilities=list(CAPABILITIES),
        fixture_auth_ref="paired:" + DEVICE,
    )
    manifest = _manifest()
    socket = FakeWebSocket()
    connection = AgentConnection(
        device_id=DEVICE,
        session_id="session-phase6",
        websocket=socket,
        protocol_version="cad.agent/2",
        capabilities=CAPABILITIES,
        capability_hash=canonical_capability_hash(CAPABILITIES),
        runtime_state="online_idle",
        document_name="drawing33.dwg",
        packages=(
            {
                "package_id": "autocad.managed_host.r25",
                "version": "0.2.0",
                "sha256": PACKAGE_SHA,
            },
        ),
        capability_manifest=manifest,
        capability_manifest_hash=CAPABILITY_SHA,
        operation_registry_hash=REGISTRY_SHA,
        registry_version="cad.program/0.2",
        write_lock_enabled=True,
        hard_pause=False,
        active_document_id=DOCUMENT,
        active_document_revision=REVISION,
    )
    await registry.add(connection)
    await service.on_agent_connected(connection)
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
    await service.repository.finalize_job_result(
        job_id=observe["job_id"],
        device_id=DEVICE,
        command_id=observe["command_id"],
        payload_hash=observe["payload_hash"],
        target="succeeded",
        result={
            "snapshot": {
                "snapshot_id": "snapshot-phase6",
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
        },
        snapshot={
            "snapshot_id": "snapshot-phase6",
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
        },
        evidence=True,
    )
    try:
        yield service, connection, socket
    finally:
        await service.shutdown()


def _prepare_request(*, key: str = "prepare-key") -> CadPrepareProgramInput:
    return CadPrepareProgramInput(
        device_id=DEVICE,
        source_snapshot_id="snapshot-phase6",
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


async def _prepare(service: DurableGatewayServices, *, key: str = "prepare-key"):
    assert service.program_service is not None
    return await service.program_service.prepare(
        _prepare_request(key=key), WRITE_PRINCIPAL, "correlation-prepare"
    )


async def _finish_preview(
    service: DurableGatewayServices,
    *,
    program_id: str,
    key: str = "preview-key",
):
    assert service.program_service is not None
    pending = await service.program_service.preview(
        CadPreviewInput(program_id=program_id, idempotency_key=key),
        WRITE_PRINCIPAL,
        "preview",
    )
    job = await service.repository.get_job(OWNER, pending.job_id)
    assert job is not None
    await service.repository.transition_job(job["job_id"], "acknowledged")
    execution = job["payload"]["execution"]
    connection = await service.registry.get(DEVICE)
    assert connection is not None
    command = connection.websocket.messages[-1]
    assert "payload" not in command
    assert command["program"]["schema_version"] == "cad.program/0.2"
    assert command["preview_id"] == execution["preview_id"]
    assert command["expires_at"] == execution["expires_at"]
    await service.job_service.handle_message(
        connection,
        ProgramResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=1,
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
    material = await service.program_repository.get_preview_by_job(
        OWNER, pending.job_id
    )
    assert material is not None
    return material


async def test_phase6_migration_is_additive_and_has_owner_scoped_records(phase6):
    service, _, _ = phase6
    with service.database.read_connection() as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "cad_programs",
        "cad_program_revisions",
        "cad_previews",
        "cad_validations",
        "cad_execution_receipts",
        "program_idempotency",
    } <= tables
    assert service.database.migration_checksums.keys() == {
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
            9,
            10,
            11,
            12,
        }


async def test_prepare_requires_scope_never_dispatches_and_is_owner_scoped(phase6):
    service, _, socket = phase6
    assert service.program_service is not None
    with pytest.raises(GatewayError) as denied:
        await service.program_service.prepare(
            _prepare_request(), READ_PRINCIPAL, "scope-denied"
        )
    assert denied.value.code == "insufficient_scope"
    prepared = await _prepare(service)
    assert prepared.ready_for_preview is True
    assert prepared.program_digest.startswith("sha256:")
    assert prepared.execution_binding["runtime_id"] == "managed_dotnet"
    assert prepared.execution_binding["host_family"] == "R25"
    assert socket.messages == []
    assert (
        await service.program_repository.get_program_revision(
            OTHER, prepared.program_id, 1
        )
        is None
    )
    duplicate = await _prepare(service)
    assert duplicate.program_id == prepared.program_id
    changed = _prepare_request()
    changed.operations[1]["end"]["x"] = 101
    with pytest.raises(GatewayError) as conflict:
        await service.program_service.prepare(
            changed, WRITE_PRINCIPAL, "prepare-conflict"
        )
    assert conflict.value.code == "idempotency_conflict"


async def test_prepare_rejects_stale_or_non_commit_safe_snapshot_before_record(phase6):
    service, connection, _ = phase6
    assert service.program_service is not None
    connection.active_document_revision = "e" * 64
    with pytest.raises(GatewayError) as stale:
        await service.program_service.prepare(
            _prepare_request(key="stale"), WRITE_PRINCIPAL, "stale"
        )
    assert stale.value.code == "stale_snapshot"
    with service.database.read_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cad_programs").fetchone()[0] == 0


async def test_gateway_program_command_validates_and_hashes_identically_for_agent(
    phase6,
):
    service, _, socket = phase6
    prepared = await _prepare(service)
    assert service.program_service is not None
    pending = await service.program_service.preview(
        CadPreviewInput(
            program_id=prepared.program_id,
            idempotency_key="wire-hash-preview",
        ),
        WRITE_PRINCIPAL,
        "wire-hash-preview",
    )
    command = ProgramCommandMessage.model_validate(socket.messages[-1])
    job = await service.repository.get_job(OWNER, pending.job_id)
    assert job is not None
    assert command.payload_hash == job["payload_hash"]
    assert command.payload_hash == program_command_payload_hash(command)
    assert command.binding.capability_manifest_hash == "sha256:" + CAPABILITY_SHA
    assert command.expires_at == job["payload"]["execution"]["expires_at"]


async def test_typed_heartbeat_updates_and_clears_phase6_write_document_state(phase6):
    service, connection, _ = phase6
    heartbeat = HeartbeatMessage(
        protocol_version="cad.agent/2",
        session_id=connection.session_id,
        device_id=DEVICE,
        sequence=1,
        paused=True,
        write_lock_enabled=False,
        hard_pause=True,
        active_document_id=None,
        active_document_revision=None,
    )
    fields = heartbeat.model_fields_set
    phase6_state_present = bool(
        {
            "write_lock_enabled",
            "hard_pause",
            "active_document_id",
            "active_document_revision",
        }
        & fields
    )
    assert await service.registry.mark_heartbeat(
        DEVICE,
        connection.session_id,
        sequence=heartbeat.sequence,
        busy=heartbeat.busy,
        current_job_id=heartbeat.current_job_id,
        paused=heartbeat.paused,
        write_lock_enabled=heartbeat.write_lock_enabled,
        hard_pause=heartbeat.hard_pause,
        active_document_id=heartbeat.active_document_id,
        active_document_revision=heartbeat.active_document_revision,
        phase6_state_present=phase6_state_present,
    )
    await service.on_agent_heartbeat(connection, heartbeat)
    assert connection.write_lock_enabled is False
    assert connection.hard_pause is True
    assert connection.active_document_id is None
    assert connection.active_document_revision is None
    device = await service.repository.get_device(OWNER, DEVICE)
    assert device is not None
    assert device["write_lock_enabled"] is False
    assert device["hard_pause"] is True
    assert device["active_document_id"] is None
    assert device["active_document_revision"] is None


async def test_preview_materializes_atomically_and_enforces_one_write_per_document(
    phase6,
):
    service, _, socket = phase6
    prepared = await _prepare(service)
    assert service.program_service is not None
    pending = await service.program_service.preview(
        CadPreviewInput(
            program_id=prepared.program_id,
            idempotency_key="preview-key",
        ),
        WRITE_PRINCIPAL,
        "preview",
    )
    assert pending.state == "dispatched"
    assert pending.preview_id is None
    assert socket.messages[-1]["kind"] == "program_preview"
    with service.database.read_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cad_previews").fetchone()[0] == 0
    with pytest.raises(GatewayError) as busy:
        await service.program_service.preview(
            CadPreviewInput(
                program_id=prepared.program_id,
                idempotency_key="second-preview",
            ),
            WRITE_PRINCIPAL,
            "preview-busy",
        )
    assert busy.value.code == "document_write_busy"
    job = await service.repository.get_job(OWNER, pending.job_id)
    assert job is not None
    await service.repository.transition_job(job["job_id"], "acknowledged")
    execution = job["payload"]["execution"]
    result = {
        "program_digest": execution["program_digest"],
        "execution_digest": execution["execution_digest"],
        "binding_digest": execution["binding_digest"],
        "preview_id": execution["preview_id"],
        "preview_digest": execution["preview_digest"],
        "expires_at": execution["expires_at"],
        "document_revision_before": REVISION,
        "document_revision_after": REVISION,
        "preview_strategy": "database_transaction_abort",
        "planned_operation_count": 2,
        "planned_entity_count": 1,
        "planned_layer_count": 1,
        "validation": {
            "transaction_aborted": True,
            "drawing_unchanged": True,
            "bounds_valid": True,
        },
    }
    for field, wrong_value, error in (
        ("preview_id", "preview-wrong", "binding_mismatch"),
        ("preview_digest", "sha256:" + ("f" * 64), "binding_mismatch"),
        (
            "expires_at",
            (
                datetime.fromisoformat(execution["expires_at"])
                + timedelta(seconds=1)
            ).isoformat(),
            "program_result_invalid",
        ),
    ):
        invalid = dict(result)
        invalid[field] = wrong_value
        with pytest.raises(RepositoryConflict, match=error):
            await service.program_repository.finalize_program_job(
                job_id=job["job_id"],
                device_id=DEVICE,
                command_id=job["command_id"],
                payload_hash=job["payload_hash"],
                target="succeeded",
                result=invalid,
                error_code=None,
                error_summary=None,
                session_id="session-phase6",
                agent_sequence=1,
            )
    await asyncio.sleep(0.01)
    await service.program_repository.finalize_program_job(
        job_id=job["job_id"],
        device_id=DEVICE,
        command_id=job["command_id"],
        payload_hash=job["payload_hash"],
        target="succeeded",
        result=result,
        error_code=None,
        error_summary=None,
        session_id="session-phase6",
        agent_sequence=1,
    )
    material = await service.program_repository.get_preview_by_job(
        OWNER, pending.job_id
    )
    assert material is not None
    assert material["validation"]["drawing_unchanged"] is True
    with service.database.read_connection() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM cad_program_write_locks").fetchone()[0]
            == 0
        )


async def test_runtime_policy_and_owner_changes_fail_before_dispatch(phase6):
    service, connection, socket = phase6
    prepared = await _prepare(service)
    assert service.program_service is not None
    sent = len(socket.messages)
    connection.operation_registry_hash = "f" * 64
    with pytest.raises(GatewayError) as mismatch:
        await service.program_service.preview(
            CadPreviewInput(
                program_id=prepared.program_id,
                idempotency_key="changed-registry",
            ),
            WRITE_PRINCIPAL,
            "changed-registry",
        )
    assert mismatch.value.code == "binding_mismatch"
    assert len(socket.messages) == sent
    with pytest.raises(GatewayError) as hidden:
        await service.program_service.preview(
            CadPreviewInput(
                program_id=prepared.program_id,
                idempotency_key="other-owner",
            ),
            Principal(subject=OTHER, scopes=("autocad.write",)),
            "other-owner",
        )
    assert hidden.value.code == "not_found"
    assert len(socket.messages) == sent


async def test_commit_exact_duplicate_returns_prior_receipt_without_second_effect(
    phase6,
):
    service, connection, socket = phase6
    prepared = await _prepare(service)
    preview = await _finish_preview(service, program_id=prepared.program_id)
    assert service.program_service is not None
    commit = await service.program_service.commit(
        CadCommitInput(
            preview_id=preview["preview_id"],
            idempotency_key="commit-key",
        ),
        WRITE_PRINCIPAL,
        "commit",
    )
    assert commit.state == "dispatched"
    commit_messages = [
        item for item in socket.messages if item.get("kind") == "program_commit"
    ]
    assert len(commit_messages) == 1
    job = await service.repository.get_job(OWNER, commit.job_id)
    assert job is not None
    await service.repository.transition_job(job["job_id"], "acknowledged")
    execution = job["payload"]["execution"]
    assert execution["receipt_id"].startswith("AUTOCAD_MCP_PROGRAM_")
    connection.active_document_revision = "e" * 64
    command = commit_messages[0]
    assert command["receipt_id"] == execution["receipt_id"]
    await service.job_service.handle_message(
        connection,
        ProgramResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=2,
            kind="program_commit",
            status="succeeded",
            payload_hash=job["payload_hash"],
            binding=command["binding"],
            result={
                "receipt_id": execution["receipt_id"],
                "receipt_digest": "sha256:" + ("2" * 64),
                "document_revision_before": REVISION,
                "document_revision_after": connection.active_document_revision,
                "created_entity_count": 1,
                "duplicate": False,
            },
        ),
    )
    exact = await service.program_service.commit(
        CadCommitInput(
            preview_id=preview["preview_id"],
            idempotency_key="commit-key",
        ),
        WRITE_PRINCIPAL,
        "commit-duplicate",
    )
    assert exact.receipt_id == execution["receipt_id"]
    assert exact.duplicate is True
    alternate_key = await service.program_service.commit(
        CadCommitInput(
            preview_id=preview["preview_id"],
            idempotency_key="commit-new-key",
        ),
        WRITE_PRINCIPAL,
        "commit-prior-receipt",
    )
    assert alternate_key.receipt_id == execution["receipt_id"]
    assert alternate_key.duplicate is True
    assert len(
        [item for item in socket.messages if item.get("kind") == "program_commit"]
    ) == 1
    with service.database.read_connection() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM cad_execution_receipts"
            ).fetchone()[0]
            == 1
        )
    validation_pending = await service.program_service.validate(
        CadValidateInput(
            receipt_id=execution["receipt_id"],
            idempotency_key="validate-key",
        ),
        WRITE_PRINCIPAL,
        "validate",
    )
    assert validation_pending.state == "dispatched"
    validation_job = await service.repository.get_job(
        OWNER, validation_pending.job_id
    )
    assert validation_job is not None
    await service.repository.transition_job(
        validation_job["job_id"], "acknowledged"
    )
    validation_command = [
        item for item in socket.messages if item.get("kind") == "program_validate"
    ][0]
    assert "program" not in validation_command
    assert validation_command["validation"]["receipt_id"] == execution["receipt_id"]
    assert (
        validation_command["validation"]["validation_id"]
        == validation_job["payload"]["execution"]["validation_id"]
    )
    await service.job_service.handle_message(
        connection,
        ProgramResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=validation_job["job_id"],
            command_id=validation_job["command_id"],
            sequence=3,
            kind="program_validate",
            status="succeeded",
            payload_hash=validation_job["payload_hash"],
            binding=validation_command["binding"],
            result={
                "validation_id": validation_job["payload"]["execution"][
                    "validation_id"
                ],
                "valid": True,
                "document_revision": connection.active_document_revision,
                "checks": ["entity_count", "document_revision", "receipt_binding"],
                "failures": [],
            },
        ),
    )
    validation = await service.program_service.validate(
        CadValidateInput(
            receipt_id=execution["receipt_id"],
            idempotency_key="validate-key",
        ),
        WRITE_PRINCIPAL,
        "validate-duplicate",
    )
    assert validation.validation_id is not None
    assert validation.passed is True
    assert validation.report["failures"] == []
    assert len(
        [item for item in socket.messages if item.get("kind") == "program_validate"]
    ) == 1


async def test_unknown_commit_keeps_document_locked_until_reconciliation(phase6):
    service, connection, socket = phase6
    prepared = await _prepare(service)
    preview = await _finish_preview(service, program_id=prepared.program_id)
    assert service.program_service is not None
    pending = await service.program_service.commit(
        CadCommitInput(
            preview_id=preview["preview_id"],
            idempotency_key="commit-unknown",
        ),
        WRITE_PRINCIPAL,
        "commit-unknown",
    )
    job = await service.repository.get_job(OWNER, pending.job_id)
    assert job is not None
    await service.repository.transition_job(job["job_id"], "acknowledged")
    execution = job["payload"]["execution"]
    command = [
        item for item in socket.messages if item.get("kind") == "program_commit"
    ][0]

    await service.job_service.handle_message(
        connection,
        ProgramResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=2,
            kind="program_commit",
            status="succeeded",
            payload_hash=job["payload_hash"],
            binding=command["binding"],
            result={
                "receipt_id": "wrong-receipt",
                "receipt_digest": "sha256:" + ("2" * 64),
                "document_revision_before": "wrong-revision",
                "document_revision_after": "e" * 64,
                "created_entity_count": 1,
                "duplicate": False,
            },
        ),
    )

    updated = await service.repository.get_job(OWNER, job["job_id"])
    assert updated is not None
    assert updated["state"] == "outcome_unknown"
    with service.database.read_connection() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM cad_program_write_locks").fetchone()[0]
            == 1
        )
    with pytest.raises(GatewayError) as busy:
        await service.program_service.preview(
            CadPreviewInput(
                program_id=prepared.program_id,
                idempotency_key="blocked-after-unknown",
            ),
            WRITE_PRINCIPAL,
            "blocked-after-unknown",
        )
    assert busy.value.code == "document_write_busy"

    with service.database.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET deadline_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job["job_id"]),
        )
    await service.job_service.sweep_deadlines()
    expired = await service.repository.get_job(OWNER, job["job_id"])
    assert expired is not None
    assert expired["state"] == "outcome_unknown"
    recoverable = await service.repository.all_nonterminal_jobs()
    assert job["job_id"] in {item["job_id"] for item in recoverable}

    await service.job_service.handle_reconcile_result(
        connection,
        ReconcileResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=3,
            status="terminal",
            payload_hash=job["payload_hash"],
            result_status="failed",
            error_code="outcome_unknown",
            error_message="Agent command outcome remains unknown",
            kind="program_commit",
            binding=command["binding"],
        ),
    )
    still_unknown = await service.repository.get_job(OWNER, job["job_id"])
    assert still_unknown is not None
    assert still_unknown["state"] == "outcome_unknown"

    await service.job_service.handle_reconcile_result(
        connection,
        ReconcileResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=4,
            status="terminal",
            payload_hash=job["payload_hash"],
            result_status="succeeded",
            result={
                "receipt_id": execution["receipt_id"],
                "receipt_digest": "sha256:" + ("3" * 64),
                "document_revision_before": REVISION,
                "document_revision_after": "e" * 64,
                "created_entity_count": 1,
                "duplicate": False,
            },
        ),
    )
    missing_binding = await service.repository.get_job(OWNER, job["job_id"])
    assert missing_binding is not None
    assert missing_binding["state"] == "outcome_unknown"

    mismatched_binding = dict(command["binding"])
    mismatched_binding["document_revision"] = "f" * 64
    await service.job_service.handle_reconcile_result(
        connection,
        ReconcileResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=5,
            status="terminal",
            payload_hash=job["payload_hash"],
            result_status="succeeded",
            kind="program_commit",
            binding=mismatched_binding,
            result={
                "receipt_id": execution["receipt_id"],
                "receipt_digest": "sha256:" + ("3" * 64),
                "document_revision_before": REVISION,
                "document_revision_after": "e" * 64,
                "created_entity_count": 1,
                "duplicate": False,
            },
        ),
    )
    mismatch = await service.repository.get_job(OWNER, job["job_id"])
    assert mismatch is not None
    assert mismatch["state"] == "outcome_unknown"
    with service.database.read_connection() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM cad_program_write_locks").fetchone()[0]
            == 1
        )

    await service.job_service.handle_reconcile_result(
        connection,
        ReconcileResultMessage(
            session_id=connection.session_id,
            device_id=DEVICE,
            job_id=job["job_id"],
            command_id=job["command_id"],
            sequence=6,
            status="terminal",
            payload_hash=job["payload_hash"],
            result_status="succeeded",
            kind="program_commit",
            binding=command["binding"],
            result={
                "receipt_id": execution["receipt_id"],
                "receipt_digest": "sha256:" + ("3" * 64),
                "document_revision_before": REVISION,
                "document_revision_after": "e" * 64,
                "created_entity_count": 1,
                "duplicate": False,
            },
        ),
    )
    reconciled = await service.repository.get_job(OWNER, job["job_id"])
    assert reconciled is not None
    assert reconciled["state"] == "succeeded"
    with service.database.read_connection() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM cad_program_write_locks").fetchone()[0]
            == 0
        )
    next_preview = await service.program_service.preview(
        CadPreviewInput(
            program_id=prepared.program_id,
            idempotency_key="allowed-after-reconcile",
        ),
        WRITE_PRINCIPAL,
        "allowed-after-reconcile",
    )
    assert next_preview.state == "dispatched"


async def test_feature_flags_default_off_and_forbid_lt_or_high_risk(tmp_path):
    config = GatewayConfig()
    assert config.program_v0_enabled is False
    assert config.managed_write_enabled is False
    assert config.lt_write_enabled is False
    assert config.high_risk_enabled is False
    with pytest.raises(ValueError, match="forbids LT write"):
        GatewayConfig(lt_write_enabled=True).validate()
    with pytest.raises(ValueError, match="explicit Phase 6 device allowlist"):
        GatewayConfig(
            program_v0_enabled=True,
            managed_write_enabled=True,
        ).validate()


async def test_phase6_portal_gets_are_bounded_owner_scoped_and_read_only(phase6):
    service, _, _ = phase6
    prepared = await _prepare(service)
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
    audience = "https://cad.example.test/mcp"
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
            profile="phase6_program",
            db_path="already-open.db",
            oauth_issuer=issuer,
            oauth_audience=audience,
            oauth_jwks_uri="https://issuer.example/.well-known/jwks.json",
            public_origin="https://cad.example.test",
            stateless_http=True,
            allowed_hosts=("testserver",),
            program_v0_enabled=True,
            managed_write_enabled=True,
            phase6_allowed_device_ids=(DEVICE,),
        ),
    )

    def token(subject: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": subject,
                "iss": issuer,
                "aud": audience,
                "iat": now,
                "exp": now + 600,
                "scope": "autocad.read",
            },
            private_pem,
            algorithm="RS256",
        )

    path = (
        f"/api/portal/v1/programs/{prepared.program_id}/revisions/"
        f"{prepared.program_revision}"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        release_status = await client.get(
            "/api/portal/v1/phase6/status",
            headers={"Authorization": f"Bearer {token('owner-a')}"},
        )
        owned = await client.get(
            path, headers={"Authorization": f"Bearer {token('owner-a')}"}
        )
        hidden = await client.get(
            path, headers={"Authorization": f"Bearer {token('owner-b')}"}
        )
        mutation = await client.post(
            path, headers={"Authorization": f"Bearer {token('owner-a')}"}
        )
    assert release_status.status_code == 200
    assert release_status.json() == {
        "program_v0_enabled": True,
        "managed_write_enabled": True,
        "kill_switch_active": False,
    }
    assert owned.status_code == 200
    assert owned.json()["program_id"] == prepared.program_id
    assert "semantic" not in owned.json()
    assert hidden.status_code == 404
    assert mutation.status_code in {404, 405}
    routed_app = app
    while not hasattr(routed_app, "routes"):
        routed_app = routed_app.app
    paths = {
        getattr(route, "path", "")
        for route in routed_app.routes
    }
    assert not any("approve" in path and "program" in path for path in paths)


def _descriptor(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _schema_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


async def test_phase6_tool_and_resource_schema_snapshots(phase6):
    service, _, _ = phase6
    server = build_mcp_server(service)
    async with Client(server) as client:
        tools = [_descriptor(item) for item in await client.list_tools()]
        resources = [
            _descriptor(item) for item in await client.list_resource_templates()
        ]
    actual_tools = [
        {
            "name": item["name"],
            "annotations": item["annotations"],
            "input_schema_sha256": _schema_hash(item["inputSchema"]),
            "output_schema_sha256": _schema_hash(item["outputSchema"]),
        }
        for item in tools
    ]
    expected_tools = json.loads(
        (SNAPSHOTS / "phase6_tools.json").read_text(encoding="utf-8")
    )
    expected_resources = json.loads(
        (SNAPSHOTS / "phase6_resources.json").read_text(encoding="utf-8")
    )
    assert actual_tools == expected_tools
    assert resources == expected_resources
    names = {item["name"] for item in tools}
    assert {
        "cad_prepare_program",
        "cad_preview",
        "cad_commit",
        "cad_validate",
    } <= names
    assert "cad_approve" not in names
