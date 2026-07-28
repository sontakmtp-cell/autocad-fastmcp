from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastmcp import Client

import autocad_gateway.app as gateway_app
from autocad_contracts import (
    Phase8CapabilityEvidence,
    ProgramCommandMessage,
    build_execution_binding_v1,
    canonical_capability_hash,
    canonical_phase8_capability_evidence_digest,
    program_command_payload_hash,
)
from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.app import GatewayConfig, build_mcp_server
from autocad_gateway.composition import build_services
from autocad_gateway.contracts import CadPrepareProgramInput, Principal
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase8_repository import (
    Phase8Repository,
)
from autocad_gateway.infrastructure.sqlite.repositories import (
    RepositoryConflict,
    SqliteRepository,
)
from autocad_gateway.phase8_contract_adapter import (
    AutocadContractsPhase8Compiler,
    AutocadContractsPhase8Revision,
    COMPILER_CORE_OPERATION_PACK,
    CREATE_EQUIVALENT_OPERATION_PACK,
    Phase8CompilerSettings,
    TRANSFORM_EXACT_OPERATION_PACK,
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
        operation_pack_allowlist=(
            COMPILER_CORE_OPERATION_PACK,
            CREATE_EQUIVALENT_OPERATION_PACK,
        ),
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


def _move_source() -> dict:
    zero = {
        "op": "literal",
        "value": {"type": "length", "value": "0", "unit": "mm"},
    }
    return {
        "schema_version": "cad.program/1.0",
        "registry_version": "cad.program/1.0-phase8-core",
        "program_id": "program-move-v1",
        "program_revision": 1,
        "device_id": "device-a",
        "source_snapshot_id": "snapshot-move",
        "document_id": "document-1",
        "expected_document_revision": "revision-before",
        "variables": [],
        "operations": [
            {
                "kind": "move_entity",
                "operation_id": "move-line",
                "target_ref_id": "ref-line",
                "displacement": {
                    "x": {
                        "op": "literal",
                        "value": {
                            "type": "length",
                            "value": "10",
                            "unit": "mm",
                        },
                    },
                    "y": zero,
                    "z": zero,
                },
            }
        ],
        "required_capabilities": ["cad.program.v1.compile"],
        "validation_profiles": ["geometry.basic.1"],
        "artifact_refs": [],
        "component_refs": [],
    }


def _move_flags() -> Phase8FeatureFlags:
    base = Phase8FeatureFlags(
        source_enabled=True,
        compiler_enabled=True,
        transform_pack_enabled=True,
        checkpoint_v2_enabled=True,
        operation_pack_allowlist=(
            COMPILER_CORE_OPERATION_PACK,
            TRANSFORM_EXACT_OPERATION_PACK,
        ),
        rollout_policy_epoch=1,
    )
    return Phase8FeatureFlags(
        **{
            **base.__dict__,
            "rollout_policy_digest": canonical_rollout_policy_digest(base),
        }
    )


def _move_ref(owner: str = "owner-a") -> dict:
    return {
        "ref_id": "ref-line",
        "owner_id": owner,
        "device_id": "device-a",
        "document_id": "document-1",
        "snapshot_id": "snapshot-move",
        "document_revision": "revision-before",
        "entity_id": "ref-line",
        "entity_type": "LINE",
        "fingerprint": _sha("d"),
    }


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, value: dict) -> None:
        self.messages.append(value)

    async def close(self, **_: object) -> None:
        return None


async def _seed_move_snapshot(
    service: DurableGatewayServices,
    *,
    snapshot_id: str,
    document_revision: str,
    fingerprint: str,
) -> None:
    observe = await service.repository.create_job(
        owner_subject="owner-a",
        device_id="device-a",
        kind="observe",
        effect_class="read",
        payload={"observation_level": "detail"},
        idempotency_key=f"observe-{snapshot_id}",
        deadline_at=None,
    )
    await service.repository.claim_job(observe["job_id"])
    await service.repository.transition_job(observe["job_id"], "acknowledged")
    snapshot = {
        "snapshot_id": snapshot_id,
        "document_revision": document_revision,
        "observation_level": "detail",
        "drawing": {
            "document_id": "document-1",
            "document_name": "drawing33.dwg",
        },
        "entity_summary": {"entity_count": 1},
        "entities": [
            {
                "entity_id": "ref-line",
                "entity_type": "LINE",
                "layer": "0",
                "fingerprint": fingerprint,
            }
        ],
        "revision_evidence": {
            "revision_schema": "cad.revision/1",
            "revision_strength": "database_object_fingerprint",
            "commit_safe": True,
        },
    }
    await service.repository.finalize_job_result(
        job_id=observe["job_id"],
        device_id="device-a",
        command_id=observe["command_id"],
        payload_hash=observe["payload_hash"],
        target="succeeded",
        result={"snapshot": snapshot},
        snapshot=snapshot,
    )


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
    assert isinstance(
        services.phase8_gateway.revision_adapter,
        AutocadContractsPhase8Revision,
    )


def test_real_compiler_derives_transform_pack_and_medium_risk():
    flags = _move_flags()
    compiled = AutocadContractsPhase8Compiler(_settings(flags)).compile(
        _move_source(),
        materialized_target_refs=[_move_ref()],
        materialized_owner_id="owner-a",
    )

    assert compiled.risk_class == "medium"
    assert compiled.checkpoint_strategy == "cad.rollback.checkpoint/2"
    assert compiled.operation_packs == (
        COMPILER_CORE_OPERATION_PACK,
        TRANSFORM_EXACT_OPERATION_PACK,
    )


@pytest.mark.asyncio
async def test_public_prepare_accepts_move_plan_and_returns_medium_risk(tmp_path):
    database = SqliteDatabase(tmp_path / "phase8-public-move.db")
    registry = ConnectionRegistry()
    flags = _move_flags()
    service = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase8_program",
        program_enabled=True,
        managed_write_enabled=True,
        allowed_write_device_ids=("device-a",),
        program_policy_version="phase8-policy/1",
        phase8_feature_flags=flags,
        phase8_compiler=AutocadContractsPhase8Compiler(_settings(flags)),
    )
    await service.initialize()
    try:
        await service.repository.seed_device(
            owner_subject="owner-a",
            device_id="device-a",
            display_name="Mechanical 2025",
            capabilities=["observe"],
            fixture_auth_ref="paired:device-a",
        )
        observe = await service.repository.create_job(
            owner_subject="owner-a",
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={"observation_level": "detail"},
            idempotency_key="observe-public-move",
            deadline_at=None,
        )
        await service.repository.claim_job(observe["job_id"])
        await service.repository.transition_job(observe["job_id"], "acknowledged")
        snapshot = {
            "snapshot_id": "snapshot-move",
            "document_revision": "revision-before",
            "observation_level": "detail",
            "drawing": {
                "document_id": "document-1",
                "document_name": "drawing33.dwg",
            },
            "entity_summary": {"entity_count": 1},
            "entities": [
                {
                    "entity_id": "ref-line",
                    "entity_type": "LINE",
                    "layer": "0",
                    "fingerprint": _sha("d"),
                }
            ],
            "revision_evidence": {
                "revision_schema": "cad.revision/1",
                "revision_strength": "database_object_fingerprint",
                "commit_safe": True,
            },
        }
        await service.repository.finalize_job_result(
            job_id=observe["job_id"],
            device_id="device-a",
            command_id=observe["command_id"],
            payload_hash=observe["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot},
            snapshot=snapshot,
        )
        source = _move_source()
        output = await service.prepare_program(
            CadPrepareProgramInput(
                device_id="device-a",
                source_snapshot_id="snapshot-move",
                operations=source["operations"],
            ),
            Principal(
                subject="owner-a",
                scopes=("autocad.read", "autocad.write"),
            ),
            "public-move",
            schema_version="cad.program/1.0",
            program_v1_source=source,
        )

        assert output.risk_class == "medium"
        assert output.ready_for_preview is True
        plan = await service.phase8_repository.get_plan(
            "owner-a", output.execution_plan_id
        )
        assert plan["operation_packs"] == [
            COMPILER_CORE_OPERATION_PACK,
            TRANSFORM_EXACT_OPERATION_PACK,
        ]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_public_prepare_patch_and_rebase_rematerialize_move_target(
    tmp_path, monkeypatch
):
    database = SqliteDatabase(tmp_path / "phase8-public-revision.db")
    registry = ConnectionRegistry()
    flags = _move_flags()
    service = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase8_program",
        program_enabled=True,
        managed_write_enabled=True,
        allowed_write_device_ids=("device-a",),
        program_policy_version="phase8-policy/1",
        phase8_feature_flags=flags,
        phase8_compiler=AutocadContractsPhase8Compiler(_settings(flags)),
        phase8_revision_adapter=AutocadContractsPhase8Revision(),
    )
    await service.initialize()
    principal = Principal(
        subject="owner-a",
        scopes=("autocad.read", "autocad.write"),
    )
    try:
        await service.repository.seed_device(
            owner_subject="owner-a",
            device_id="device-a",
            display_name="Mechanical 2025",
            capabilities=["observe"],
            fixture_auth_ref="paired:device-a",
        )
        await _seed_move_snapshot(
            service,
            snapshot_id="snapshot-move",
            document_revision="revision-before",
            fingerprint=_sha("d"),
        )
        source = _move_source()
        root = await service.prepare_program(
            CadPrepareProgramInput(
                device_id="device-a",
                source_snapshot_id="snapshot-move",
                operations=source["operations"],
            ),
            principal,
            "revision-root",
            schema_version="cad.program/1.0",
            program_v1_source=source,
        )
        monkeypatch.setattr(
            gateway_app,
            "_principal",
            lambda *_args, **_kwargs: principal,
        )
        client = Client(build_mcp_server(service))
        async with client:
            root_resource = await client.read_resource(root.resource_uri)
        assert json.loads(root_resource[0].text)["revision"] == 1

        patched_operations = _move_source()["operations"]
        patched_operations[0]["displacement"]["x"]["value"]["value"] = "25"
        missing_target_operations = _move_source()["operations"]
        missing_target_operations[0]["target_ref_id"] = "missing-ref"
        async with client:
            rejected = await client.call_tool(
                "cad_prepare_program",
                {
                    "schema_version": "cad.program/1.0",
                    "program_v1_revision_request": {
                        "kind": "patch",
                        "program_id": root.program_id,
                        "source_revision": 1,
                        "changes": {"operations": missing_target_operations},
                    },
                },
                raise_on_error=False,
            )
        assert rejected.is_error
        assert (
            await service.phase8_repository.get_revision(
                "owner-a", root.program_id, 2
            )
            is None
        )
        async with client:
            patched_result = await client.call_tool(
                "cad_prepare_program",
                {
                    "schema_version": "cad.program/1.0",
                    "program_v1_revision_request": {
                        "kind": "patch",
                        "program_id": root.program_id,
                        "source_revision": 1,
                        "changes": {"operations": patched_operations},
                    },
                },
            )
        patched = patched_result.structured_content
        assert patched["program_revision"] == 2
        patched_plan = await service.phase8_repository.get_plan(
            "owner-a", patched["execution_plan_id"]
        )
        assert patched_plan["plan"]["materialized_target_refs"][0][
            "snapshot_id"
        ] == "snapshot-move"

        await _seed_move_snapshot(
            service,
            snapshot_id="snapshot-move-new",
            document_revision="revision-after",
            fingerprint=_sha("d"),
        )
        async with client:
            rebased_result = await client.call_tool(
                "cad_prepare_program",
                {
                    "schema_version": "cad.program/1.0",
                    "program_v1_revision_request": {
                        "kind": "rebase",
                        "program_id": root.program_id,
                        "source_revision": 2,
                        "new_snapshot_id": "snapshot-move-new",
                    },
                },
            )
        rebased = rebased_result.structured_content
        assert rebased["program_revision"] == 3
        assert rebased["expected_document_revision"] == "revision-after"
        rebased_plan = await service.phase8_repository.get_plan(
            "owner-a", rebased["execution_plan_id"]
        )
        assert rebased_plan["plan"]["materialized_target_refs"][0][
            "snapshot_id"
        ] == "snapshot-move-new"
        revision = await service.phase8_repository.get_revision(
            "owner-a", root.program_id, 3
        )
        assert revision["lineage_kind"] == "rebase"
        assert revision["parent_revision"] == 2

        await _seed_move_snapshot(
            service,
            snapshot_id="snapshot-move-conflict",
            document_revision="revision-conflict",
            fingerprint=_sha("e"),
        )
        async with client:
            conflict_result = await client.call_tool(
                "cad_prepare_program",
                {
                    "schema_version": "cad.program/1.0",
                    "program_v1_revision_request": {
                        "kind": "rebase",
                        "program_id": root.program_id,
                        "source_revision": 3,
                        "new_snapshot_id": "snapshot-move-conflict",
                    },
                },
            )
        conflict = conflict_result.structured_content
        assert conflict["lineage_kind"] == "rebase"
        assert conflict["program_revision"] == 4
        assert conflict["ready_for_preview"] is False
        report = await service.phase8_repository.get_conflict_report(
            "owner-a", conflict["conflict_report_id"]
        )
        assert report["conflicts"] == [
            {
                "code": "target_fingerprint_changed",
                "ref_id": "ref-line",
            }
        ]
        async with client:
            conflict_resource = await client.read_resource(
                conflict["resource_uri"]
            )
        assert json.loads(conflict_resource[0].text)["conflict_report"][
            "conflicts"
        ] == report["conflicts"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_transform_admission_matches_each_capability_to_its_pack(tmp_path):
    database = SqliteDatabase(tmp_path / "phase8-transform-admission.db")
    await database.open()
    seed_parent_rows(database)
    repository = Phase8Repository(database)
    flags = _move_flags()
    service = Phase8GatewayService(
        repository,
        flags,
        compiler=AutocadContractsPhase8Compiler(_settings(flags)),
    )
    try:
        source = _move_source()
        source["source_snapshot_id"] = "snapshot-1"
        target_ref = _move_ref()
        target_ref["snapshot_id"] = "snapshot-1"
        prepared = await service.prepare_root(
            owner_subject="owner-a",
            program_id="program-move-v1",
            device_id="device-a",
            document_id="document-1",
            source_snapshot_id="snapshot-1",
            expected_document_revision="revision-before",
            source=source,
            materialized_target_refs=[target_ref],
        )
        plan = prepared["plan"]
        now = datetime.now(timezone.utc)
        packs = {
            "cad.program.v1.compile": (COMPILER_CORE_OPERATION_PACK, "ALL"),
            "cad.op.move.line.v1": (COMPILER_CORE_OPERATION_PACK, "LINE"),
        }
        move_evidence = None
        for index, capability in enumerate(plan["required_capabilities"], start=1):
            operation_pack, entity_type = packs[capability]
            evidence = {
                "schema_version": "cad.capability-evidence/1",
                "evidence_id": f"evidence-transform-{index}",
                "evidence_authority": "gateway_server",
                "owner_subject": "owner-a",
                "device_id": "device-a",
                "capability_key": capability,
                "operation_pack": operation_pack,
                "runtime_id": "managed_dotnet",
                "host_family": "R25",
                "entity_type": entity_type,
                "support_state": "lab_commit",
                "package_hash": _sha("1"),
                "capability_manifest_hash": _sha("2"),
                "operation_registry_hash": _sha("3"),
                "package_signature_verified": True,
                "agent_evidence_digest": _sha("4"),
                "host_evidence_digest": _sha("5"),
                "cohort": "lab",
                "evidence_version": "1",
                "issued_at": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
            }
            wire = {
                key: value
                for key, value in evidence.items()
                if key != "owner_subject"
            }
            evidence["evidence_digest"] = (
                canonical_phase8_capability_evidence_digest(wire)
            )
            await repository.record_capability_evidence(evidence)
            if capability == "cad.op.move.line.v1":
                move_evidence = evidence

        with pytest.raises(RepositoryConflict, match="capability_missing"):
            await service.admit(
                owner_subject="owner-a",
                device_id="device-a",
                plan_id=plan["plan_id"],
                action="commit",
                cohort="lab",
                reported_capabilities=tuple(plan["required_capabilities"]),
                current_runtime_pins=plan["runtime_pins"],
            )

        assert move_evidence is not None
        correct_move_evidence = {
            **move_evidence,
            "evidence_id": "evidence-transform-correct",
            "operation_pack": TRANSFORM_EXACT_OPERATION_PACK,
        }
        correct_wire = {
            key: value
            for key, value in correct_move_evidence.items()
            if key not in {"owner_subject", "evidence_digest"}
        }
        correct_move_evidence["evidence_digest"] = (
            canonical_phase8_capability_evidence_digest(correct_wire)
        )
        await repository.record_capability_evidence(correct_move_evidence)

        admitted = await service.admit(
            owner_subject="owner-a",
            device_id="device-a",
            plan_id=plan["plan_id"],
            action="commit",
            cohort="lab",
            reported_capabilities=tuple(plan["required_capabilities"]),
            current_runtime_pins=plan["runtime_pins"],
        )

        assert len(admitted["capability_evidence_ids"]) == 2
        assert {
            item["operation_pack"]
            for item in admitted["capability_evidence"]
        } == {
            COMPILER_CORE_OPERATION_PACK,
            TRANSFORM_EXACT_OPERATION_PACK,
        }
    finally:
        await database.close()


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
        "operation_pack": COMPILER_CORE_OPERATION_PACK,
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
