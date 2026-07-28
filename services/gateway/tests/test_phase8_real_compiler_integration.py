from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autocad_contracts import (
    Phase8CapabilityEvidence,
    ProgramCommandMessage,
    build_execution_binding_v1,
    canonical_capability_hash,
    canonical_phase8_capability_evidence_digest,
    program_command_payload_hash,
)
from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.app import GatewayConfig
from autocad_gateway.composition import build_services
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase8_repository import (
    Phase8Repository,
)
from autocad_gateway.infrastructure.sqlite.repositories import SqliteRepository
from autocad_gateway.phase8_contract_adapter import (
    AutocadContractsPhase8Compiler,
    CREATE_CORE_OPERATION_PACK,
    Phase8CompilerSettings,
)
from autocad_gateway.phase8_gateway import (
    Phase8FeatureFlags,
    Phase8GatewayService,
    canonical_rollout_policy_digest,
)
from test_phase7_domain_storage import seed_parent_rows


def _sha(character: str) -> str:
    return "sha256:" + character * 64


def _flags() -> Phase8FeatureFlags:
    base = Phase8FeatureFlags(
        source_enabled=True,
        compiler_enabled=True,
        create_pack_enabled=True,
        operation_pack_allowlist=(CREATE_CORE_OPERATION_PACK,),
        rollout_policy_epoch=1,
    )
    return Phase8FeatureFlags(
        **{
            **base.__dict__,
            "rollout_policy_digest": canonical_rollout_policy_digest(base),
        }
    )


def _settings(flags: Phase8FeatureFlags) -> Phase8CompilerSettings:
    return Phase8CompilerSettings(
        compiler_package_hash=_sha("7"),
        runtime_id="managed_dotnet",
        host_family="R25",
        host_version="0.8.0",
        package_id="autocad.managed_host.r25",
        package_version="0.8.0",
        package_hash=_sha("1"),
        capability_manifest_hash=_sha("2"),
        operation_registry_version="cad.program/1.0-create-core",
        operation_registry_hash=_sha("3"),
        policy_version="phase8-policy/1",
        rollout_policy_digest=canonical_rollout_policy_digest(flags),
    )


def _source() -> dict:
    literal = {
        "op": "literal",
        "value": {"type": "length", "value": "0", "unit": "mm"},
    }
    return {
        "schema_version": "cad.program/1.0",
        "registry_version": "cad.program/1.0-create-core",
        "program_id": "program-real-v1",
        "program_revision": 1,
        "device_id": "device-a",
        "source_snapshot_id": "snapshot-1",
        "document_id": "document-1",
        "expected_document_revision": "revision-before",
        "variables": [],
        "operations": [
            {
                "kind": "ensure_layer",
                "operation_id": "layer-main",
                "name": "MCP-PHASE8",
            },
            {
                "kind": "create_line",
                "operation_id": "line-main",
                "layer": {
                    "operation_id": "layer-main",
                    "output": "layer",
                },
                "start": {"x": literal, "y": literal, "z": literal},
                "end": {
                    "x": {
                        "op": "literal",
                        "value": {
                            "type": "length",
                            "value": "100",
                            "unit": "mm",
                        },
                    },
                    "y": literal,
                    "z": literal,
                },
            },
        ],
        "required_capabilities": ["cad.program.v1.compile"],
        "validation_profiles": ["geometry.basic.1"],
        "artifact_refs": [],
        "component_refs": [],
    }


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, value: dict) -> None:
        self.messages.append(value)

    async def close(self, **_: object) -> None:
        return None


def test_phase8_composition_injects_the_real_compiler(tmp_path):
    config = GatewayConfig(
        profile="phase8_program",
        db_path=str(tmp_path / "phase8-composition.db"),
        oauth_issuer="https://issuer.test/",
        oauth_audience="https://gateway.test",
        oauth_jwks_uri="https://issuer.test/.well-known/jwks.json",
        public_origin="https://gateway.test",
        phase7_c2_enabled=True,
        program_v1_source_enabled=True,
        program_v1_compiler_enabled=True,
        program_v1_create_pack_enabled=True,
        phase8_rollout_policy_epoch=1,
        phase8_compiler_package_hash=_sha("7"),
        phase8_package_id="autocad.managed_host.r25",
        phase8_package_version="0.8.0",
        phase8_package_hash=_sha("1"),
        phase8_capability_manifest_hash=_sha("2"),
        phase8_operation_registry_hash=_sha("3"),
    ).validate()
    services = build_services(config)
    assert isinstance(
        services.phase8_gateway.compiler,
        AutocadContractsPhase8Compiler,
    )


@pytest.mark.asyncio
async def test_real_compiler_seals_exact_plan_and_is_owner_scoped(tmp_path):
    database = SqliteDatabase(tmp_path / "phase8-real.db")
    await database.open()
    seed_parent_rows(database)
    repository = Phase8Repository(database)
    flags = _flags()
    service = Phase8GatewayService(
        repository,
        flags,
        compiler=AutocadContractsPhase8Compiler(_settings(flags)),
    )
    try:
        first = await service.prepare_root(
            owner_subject="owner-a",
            program_id="program-real-v1",
            device_id="device-a",
            document_id="document-1",
            source_snapshot_id="snapshot-1",
            expected_document_revision="revision-before",
            source=_source(),
        )
        second = await service.prepare_root(
            owner_subject="owner-a",
            program_id="program-real-v1",
            device_id="device-a",
            document_id="document-1",
            source_snapshot_id="snapshot-1",
            expected_document_revision="revision-before",
            source=_source(),
        )
        plan = first["plan"]
        assert second["plan"] == plan
        assert plan["plan_digest"] == plan["plan"]["execution_plan_digest"]
        assert plan["source_digest"] == plan["plan"]["source_digest"]
        assert plan["effect_digest"] == plan["plan"]["effect_manifest_digest"]
        assert plan["plan"]["compiler"]["compiler_package_hash"] == _sha("7")
        assert (
            await repository.get_plan("owner-b", plan["plan_id"])
            is None
        )
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=2)
        ).isoformat()
        preview_binding = build_execution_binding_v1(
            plan["plan"],
            action="preview",
            preview_id="preview-owner-isolation",
            preview_expires_at=expires_at,
        ).model_dump(mode="json")
        job_repository = SqliteRepository(database)
        job = await job_repository.create_job(
            owner_subject="owner-a",
            device_id="device-a",
            kind="read",
            effect_class="read",
            payload={"purpose": "phase8-preview-owner-isolation"},
            idempotency_key="phase8-preview-owner-isolation",
            deadline_at=expires_at,
        )
        await repository.create_preview(
            owner_subject="owner-a",
            plan_id=plan["plan_id"],
            preview_id="preview-owner-isolation",
            job_id=job["job_id"],
            execution_binding=preview_binding,
            capability_evidence_ids=[],
            expires_at=expires_at,
            idempotency_key="phase8-preview-owner-isolation",
            request_digest=_sha("9"),
        )
        assert (
            await repository.get_preview(
                "owner-b", "preview-owner-isolation"
            )
            is None
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_phase8_preview_dispatches_as_typed_program_command_without_source(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase8-dispatch.db")
    await database.open()
    repository = SqliteRepository(database)
    registry = ConnectionRegistry()
    flags = _flags()
    compiled = AutocadContractsPhase8Compiler(_settings(flags)).compile(
        _source()
    )
    issued_at = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    valid_until = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    evidence_value = {
        "schema_version": "cad.capability-evidence/1",
        "evidence_id": "evidence-compile",
        "evidence_authority": "gateway_server",
        "device_id": "device-a",
        "capability_key": "cad.program.v1.compile",
        "operation_pack": CREATE_CORE_OPERATION_PACK,
        "runtime_id": "managed_dotnet",
        "host_family": "R25",
        "entity_type": "LINE",
        "support_state": "lab_commit",
        "package_hash": _sha("1"),
        "capability_manifest_hash": _sha("2"),
        "operation_registry_hash": _sha("3"),
        "package_signature_verified": True,
        "agent_evidence_digest": _sha("4"),
        "host_evidence_digest": _sha("5"),
        "cohort": "lab",
        "evidence_version": "1",
        "issued_at": issued_at,
        "valid_until": valid_until,
    }
    evidence_value["evidence_digest"] = (
        canonical_phase8_capability_evidence_digest(evidence_value)
    )
    evidence = Phase8CapabilityEvidence.model_validate(evidence_value)
    preview_id = "preview-v1-dispatch"
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=2)
    ).isoformat()
    binding = build_execution_binding_v1(
        compiled.plan,
        action="preview",
        preview_id=preview_id,
        preview_expires_at=expires_at,
    )
    payload = {
        "binding": binding.model_dump(mode="json"),
        "execution_plan": compiled.plan,
        "capability_evidence": [evidence.model_dump(mode="json")],
        "preview_id": preview_id,
        "expires_at": expires_at,
    }
    capabilities = (
        "program_preview",
        "cad.program.v1.compile",
    )
    socket = _Socket()
    connection = AgentConnection(
        device_id="device-a",
        session_id="session-phase8",
        websocket=socket,
        protocol_version="cad.agent/2",
        capabilities=capabilities,
        capability_hash=canonical_capability_hash(capabilities),
        packages=(
            {
                "package_id": "autocad.managed_host.r25",
                "version": "0.8.0",
                "sha256": "1" * 64,
            },
        ),
        capability_manifest={
            "cad_products": [
                {
                    "edition": "full",
                    "release_year": 2025,
                    "runtime": {
                        "id": "managed_dotnet",
                        "role": "primary",
                        "host_family": "R25",
                        "host_version": "0.8.0",
                    },
                }
            ]
        },
        capability_manifest_hash=_sha("2"),
        operation_registry_hash=_sha("3"),
        registry_version="cad.program/1.0-create-core",
        write_lock_enabled=True,
    )
    await registry.add(connection)
    await repository.seed_device(
        owner_subject="owner-a",
        device_id="device-a",
        display_name="Mechanical 2025",
        capabilities=list(capabilities),
        fixture_auth_ref="paired:device-a",
    )
    job = await repository.create_job(
        owner_subject="owner-a",
        device_id="device-a",
        kind="program_preview",
        effect_class="write",
        payload=payload,
        idempotency_key="preview-dispatch-key",
        deadline_at=expires_at,
    )
    service = DurableJobService(
        repository,
        registry,
        program_policy_version="phase8-policy/1",
        managed_write_enabled=True,
        allowed_write_device_ids=("device-a",),
    )
    try:
        assert await service.dispatch(
            job["job_id"], correlation_id="phase8-dispatch"
        )
        command = ProgramCommandMessage.model_validate(socket.messages[-1])
        assert command.execution_plan is not None
        assert command.program is None
        assert command.approval_binding is None
        assert "program" not in socket.messages[-1]
        assert program_command_payload_hash(command) == job["payload_hash"]
    finally:
        await database.close()
