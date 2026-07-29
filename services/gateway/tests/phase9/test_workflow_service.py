import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from autocad_gateway.durable_services import (
    DurableGatewayServices,
    _Phase9ActionPort,
)
from autocad_gateway.phase8_contract_adapter import (
    COMPILER_CORE_OPERATION_PACK,
    CREATE_EQUIVALENT_OPERATION_PACK,
)
from autocad_gateway.phase8_gateway import Phase8FeatureFlags
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase9_repository import Phase9Repository
from autocad_gateway.skills.catalog import SkillCatalog
from autocad_gateway.skills.catalog_repository import SkillCatalogRepository
from autocad_gateway.workflows.runner import WorkflowRunner
from autocad_gateway.workflows.service import (
    WorkflowApplicationService,
    WorkflowServiceError,
)

CATALOG_ROOT = Path(__file__).resolve().parents[4] / "packages" / "skill_catalog"


@pytest.mark.asyncio
async def test_phase9_device_context_maps_bounded_create_registry_pack():
    gateway = object.__new__(DurableGatewayServices)
    connection = SimpleNamespace()
    gateway._require_device = AsyncMock(
        return_value={"capabilities": {"observe"}}
    )
    gateway.registry = SimpleNamespace(
        get=AsyncMock(return_value=connection),
        is_current_and_fresh=AsyncMock(return_value=True),
    )
    gateway.is_phase8 = True
    gateway.phase8_gateway = SimpleNamespace(
        flags=Phase8FeatureFlags(
            source_enabled=True,
            compiler_enabled=True,
            create_pack_enabled=True,
            operation_pack_allowlist=(
                COMPILER_CORE_OPERATION_PACK,
                CREATE_EQUIVALENT_OPERATION_PACK,
            ),
        )
    )
    gateway.program_service = SimpleNamespace(allowed_device_ids={"device-a"})

    context = await gateway._phase9_device_context("owner-a", "device-a")

    assert "cad.program.v1.compile" in context["capabilities"]
    assert "cad.program/1.0-create-core" in context["operation_packs"]
    assert all(
        marker not in pack
        for pack in context["operation_packs"]
        for marker in ("delete", "topology")
    )

    gateway.program_service.allowed_device_ids = {"other-device"}
    disallowed = await gateway._phase9_device_context("owner-a", "device-a")
    assert "cad.program/1.0-create-core" not in disallowed["operation_packs"]


@pytest.mark.asyncio
async def test_phase8_admission_uses_pinned_runtime_manifest_capabilities():
    gateway = object.__new__(DurableGatewayServices)
    gateway.phase8_gateway = SimpleNamespace(admit=AsyncMock(return_value={}))
    pins = {
        "runtime_id": "managed_dotnet",
        "host_family": "R25",
        "package_hash": "sha256:" + "1" * 64,
    }
    connection = SimpleNamespace(
        capabilities=("program_preview", "program_commit"),
        capability_manifest={
            "cad_products": [
                {
                    "runtime": {
                        "id": "managed_dotnet",
                        "host_family": "R25",
                        "package_hash": pins["package_hash"],
                    },
                    "capabilities": [
                        "cad.program.v1.compile",
                        "cad.program.v1.preview",
                    ],
                },
                {
                    "runtime": {
                        "id": "file_ipc",
                        "host_family": "LT",
                        "package_hash": "sha256:" + "2" * 64,
                    },
                    "capabilities": ["cad.op.delete.line.v1"],
                },
            ]
        },
    )

    await gateway._phase8_admit(
        principal=SimpleNamespace(subject="owner-a"),
        plan={"plan": {"device_id": "device-a"}, "plan_id": "plan-a"},
        connection=connection,
        current_pins=pins,
        action="preview",
    )

    assert gateway.phase8_gateway.admit.await_args.kwargs[
        "reported_capabilities"
    ] == ("cad.program.v1.compile", "cad.program.v1.preview")


@pytest.mark.asyncio
async def test_phase9_write_preview_waits_for_preview_job():
    gateway = object.__new__(DurableGatewayServices)
    gateway._phase9_snapshot = AsyncMock(
        return_value={
            "document_id": "document-a",
            "document_revision": "revision-a",
            "entities": [],
        }
    )
    gateway.prepare_program = AsyncMock(
        return_value=SimpleNamespace(
            program_id="program-a",
            program_revision=1,
            model_dump=lambda **_: {"program_id": "program-a"},
        )
    )
    gateway.preview_program = AsyncMock(
        return_value=SimpleNamespace(
            job_id="job-a",
            model_dump=lambda **_: {
                "job_id": "job-a",
                "state": "queued",
                "validation": None,
            },
        )
    )
    preview_job = {"job_id": "job-a", "state": "queued"}
    gateway.repository = SimpleNamespace(
        get_job=AsyncMock(return_value=preview_job)
    )
    gateway.job_service = SimpleNamespace(
        wait_for_existing_job=AsyncMock(
            return_value={
                "job_id": "job-a",
                "state": "succeeded",
                "result": {"preview_digest": "sha256:" + "3" * 64},
            }
        )
    )

    result = await gateway._phase9_write_preview(
        "owner-a",
        "mechanical.plate-hole-pattern",
        "device-a",
        "snapshot-a",
        _plate_inputs(),
        "workflow-preview-a",
        ("autocad.write",),
    )

    assert result["preview"]["state"] == "succeeded"
    assert result["preview"]["validation"]["preview_digest"].endswith("3" * 64)
    gateway.job_service.wait_for_existing_job.assert_awaited_once_with(
        preview_job,
        owner_subject="owner-a",
        correlation_id=gateway.preview_program.await_args.args[2],
    )


@pytest.fixture
async def service(tmp_path):
    database = SqliteDatabase(tmp_path / "phase9.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    value = WorkflowApplicationService(
        Phase9Repository(database),
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=False,
        enabled_skills={"drawing.cleanup-audit"},
        device_resolver=lambda owner, device: _device(owner, device),
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
    )
    value.initialize_catalog()
    yield value
    await database.close()


async def _device(owner: str, device: str) -> dict:
    if owner != "owner-a" or device != "device-a":
        raise ValueError("not_found")
    return {
        "capabilities": set(),
        "operation_packs": set(),
        "runtime_release_verified": True,
        "capability_evidence_verified": True,
        "identity_generation": 1,
    }


async def _snapshot(owner: str, device: str, snapshot: str) -> dict:
    if (owner, device, snapshot) != ("owner-a", "device-a", "snapshot-a"):
        raise ValueError("not_found")
    return {
        "snapshot_id": snapshot,
        "device_id": device,
        "document_id": "document-a",
        "document_revision": "revision-a",
        "entities": [],
    }


def _cleanup_inputs() -> dict:
    return {
        "source_snapshot_id": "snapshot-a",
        "document_revision": "revision-a",
        "layer": "0",
        "page_size": 50,
        "max_candidates": 20,
    }


def _plate_inputs() -> dict:
    return {
        "source_snapshot_id": "snapshot-a",
        "document_revision": "revision-a",
        "layer": "MECH",
        "width": 100.0,
        "height": 60.0,
        "hole_diameter": 8.0,
        "rows": 2,
        "columns": 3,
        "margin_x": 10.0,
        "margin_y": 10.0,
        "include_overall_dimensions": True,
    }


async def _write_device(owner: str, device: str) -> dict:
    assert (owner, device) == ("owner-a", "device-a")
    return {
        "capabilities": {"cad.program.v1.compile"},
        "operation_packs": {"cad.program/1.0-create-core"},
        "runtime_release_verified": True,
        "capability_evidence_verified": True,
        "identity_generation": 1,
    }


def _auto_dimension_inputs() -> dict:
    return {
        "source_snapshot_id": "snapshot-a",
        "document_revision": "revision-a",
        "layer": "DIM",
        "entity_ids": ["entity-a"],
        "profile": "mechanical_mm",
        "offset": 10.0,
    }


async def _auto_dimension_service(
    tmp_path, *, commit_status=None, fail_preview=False
):
    database = SqliteDatabase(tmp_path / "auto-dimension.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    repository = Phase9Repository(database)
    calls = {"preview": 0, "commit": 0}

    class Port:
        async def dispatch(self, action_kind, payload, *, idempotency_key):
            calls[action_kind] += 1
            if action_kind == "preview":
                if fail_preview:
                    raise RuntimeError("preview_unavailable")
                return {
                    "observe": {"snapshot_id": "snapshot-a"},
                    "query": {"entities": []},
                    "pure": {"semantic_digest": "sha256:" + "1" * 64},
                    "prepare": {"program_id": "program-a", "program_revision": 1},
                    "preview": {"preview_id": "preview-a", "state": "succeeded"},
                }
            return {
                "state": "awaiting_approval",
                "admission_status": "approval_required",
                "intent_id": "intent-a",
            }

        async def reconcile(self, action_kind, child_ref, *, idempotency_key):
            return None

    service = WorkflowApplicationService(
        repository,
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.auto-dimension-overall"},
        device_resolver=_write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=lambda *args: None,
        commit_request_executor=lambda *args: None,
        action_runner=WorkflowRunner(repository, Port(), worker_id="worker"),
        commit_status_resolver=commit_status,
    )
    service.initialize_catalog()
    return database, service, calls


@pytest.mark.asyncio
async def test_catalog_start_materializes_pinned_dag_and_replays(service):
    listing = await service.list_skills(
        owner_subject="owner-a", device_id="device-a"
    )
    assert [skill["skill_id"] for skill in listing["skills"]] == [
        "drawing.cleanup-audit"
    ]
    first = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="start-cleanup",
        scopes=("autocad.read",),
    )
    replay = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="start-cleanup",
        scopes=("autocad.read",),
    )
    assert first["state"] == "waiting_for_user"
    assert first["pins"]["workflow_digest"].startswith("sha256:")
    assert replay["run_id"] == first["run_id"]
    assert replay["replayed"] is True
    detail = await service.get("owner-a", first["run_id"])
    assert len(detail["steps"]) == 5
    assert sum(step["state"] == "waiting" for step in detail["steps"]) == 1
    completed = await service.control(
        owner_subject="owner-a",
        run_id=first["run_id"],
        action="submit_input",
        expected_state_version=first["state_version"],
        idempotency_key="finish-cleanup",
        payload={"decision": "continue"},
    )
    assert completed["state"] == "succeeded"
    replayed_control = await service.control(
        owner_subject="owner-a",
        run_id=first["run_id"],
        action="submit_input",
        expected_state_version=first["state_version"],
        idempotency_key="finish-cleanup",
        payload={"decision": "continue"},
    )
    assert replayed_control == completed


@pytest.mark.asyncio
async def test_input_device_owner_and_write_flags_fail_closed(service):
    invalid = _cleanup_inputs()
    invalid["command"] = "erase all"
    with pytest.raises(WorkflowServiceError, match="invalid_request"):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="drawing.cleanup-audit",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=invalid,
            idempotency_key="invalid",
            scopes=("autocad.read",),
        )
    with pytest.raises(WorkflowServiceError, match="not_found"):
        await service.list_skills(owner_subject="owner-b", device_id="device-a")
    with pytest.raises(WorkflowServiceError, match="not_found"):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="mechanical.plate-hole-pattern",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs={},
            idempotency_key="write-disabled",
            scopes=("autocad.read", "autocad.write"),
        )


@pytest.mark.asyncio
async def test_cross_owner_run_is_not_found(service):
    created = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="owner-check",
        scopes=("autocad.read",),
    )
    with pytest.raises(WorkflowServiceError, match="not_found"):
        await service.get("owner-b", created["run_id"])


@pytest.mark.asyncio
async def test_submit_input_recovers_after_wait_was_already_resolved(service):
    created = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="recover-wait-start",
        scopes=("autocad.read",),
    )
    payload = {"decision": "continue"}
    await service.repository.begin_control_command(
        owner_subject="owner-a",
        run_id=created["run_id"],
        action="submit_input",
        expected_state_version=created["state_version"],
        idempotency_key="recover-wait-control",
        payload=payload,
    )
    wait = await service.repository.current_wait("owner-a", created["run_id"])
    await service.repository.resolve_wait(
        owner_subject="owner-a",
        run_id=created["run_id"],
        wait_id=wait["wait_id"],
        expected_state_version=created["state_version"],
        response_schema_digest=wait["response_schema_digest"],
        response=payload,
        idempotency_key="recover-wait-control",
    )
    recovered = await service.control(
        owner_subject="owner-a",
        run_id=created["run_id"],
        action="submit_input",
        expected_state_version=created["state_version"],
        idempotency_key="recover-wait-control",
        payload=payload,
        scopes=("autocad.read",),
    )
    assert recovered["state"] == "succeeded"


@pytest.mark.asyncio
async def test_write_workflow_reuses_preview_and_trusted_approval_ports(tmp_path):
    database = SqliteDatabase(tmp_path / "write.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)

    async def write_device(owner: str, device: str) -> dict:
        assert (owner, device) == ("owner-a", "device-a")
        return {
            "capabilities": {"cad.program.v1.compile"},
            "operation_packs": {"cad.program/1.0-create-core"},
            "runtime_release_verified": True,
            "capability_evidence_verified": True,
            "identity_generation": 1,
        }

    async def preview(*args) -> dict:
        return {
            "pure": {"semantic_digest": "sha256:" + "1" * 64},
            "prepare": {"program_id": "program-a", "program_revision": 1},
            "preview": {"preview_id": "preview-a", "state": "succeeded"},
        }

    async def commit(owner: str, preview_id: str, key: str) -> dict:
        assert owner == "owner-a"
        assert preview_id == "preview-a"
        assert key
        return {
            "admission_status": "approval_required",
            "intent_id": "intent-a",
            "consent_id": "consent-a",
        }

    repository = Phase9Repository(database)
    commit_states = [
        {"state": "queued", "intent_id": "intent-a", "job_id": "job-a"},
        {
            "state": "succeeded",
            "intent_id": "intent-a",
            "job_id": "job-a",
            "receipt_id": "receipt-a",
        },
    ]

    async def commit_status(owner, intent_id):
        assert (owner, intent_id) == ("owner-a", "intent-a")
        return commit_states.pop(0)

    class ActionPort:
        async def dispatch(self, action_kind, payload, *, idempotency_key):
            if action_kind == "preview":
                return await preview()
            return await commit(
                payload["owner_subject"], payload["preview_id"], idempotency_key
            )

        async def reconcile(self, action_kind, child_ref, *, idempotency_key):
            return {
                "state": "succeeded",
                "result": await self.dispatch(
                    action_kind,
                    child_ref["payload"],
                    idempotency_key=idempotency_key,
                ),
            }

    value = WorkflowApplicationService(
        repository,
        SkillCatalogRepository(database),
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.plate-hole-pattern"},
        device_resolver=write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=preview,
        commit_request_executor=commit,
        action_runner=WorkflowRunner(
            repository, ActionPort(), worker_id="test-worker"
        ),
        commit_status_resolver=commit_status,
    )
    value.initialize_catalog()
    result = await value.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.plate-hole-pattern",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs={
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "layer": "MECH",
            "width": 100.0,
            "height": 60.0,
            "hole_diameter": 8.0,
            "rows": 2,
            "columns": 3,
            "margin_x": 10.0,
            "margin_y": 10.0,
            "include_overall_dimensions": True,
        },
        idempotency_key="plate-start",
        scopes=("autocad.read", "autocad.write"),
    )
    assert result["state"] == "waiting_for_trusted_approval"
    detail = await value.get("owner-a", result["run_id"])
    assert detail["current_wait"]["wait_kind"] == "trusted_approval"
    assert next(
        step for step in detail["steps"] if step["step_id"] == "commit"
    )["state"] == "waiting"
    actions = await repository.list_actions("owner-a", result["run_id"])
    assert [action["state"] for action in actions] == ["completed", "completed"]
    assert actions[-1]["child_ref"]["intent_id"] == "intent-a"

    with pytest.raises(WorkflowServiceError, match="insufficient_scope"):
        await value.control(
            owner_subject="owner-a",
            run_id=result["run_id"],
            action="resume",
            expected_state_version=result["state_version"],
            idempotency_key="read-only-control",
            scopes=("autocad.read",),
        )
    await value.maintenance_once()
    waiting = await value.get("owner-a", result["run_id"])
    assert waiting["run"]["state"] == "waiting_for_job"
    await value.maintenance_once()
    completed = await value.get("owner-a", result["run_id"])
    assert completed["run"]["state"] == "succeeded"
    assert all(step["state"] == "succeeded" for step in completed["steps"])
    await database.close()


@pytest.mark.asyncio
async def test_exact_start_replay_resumes_after_atomic_create_commit(
    service, monkeypatch
):
    original = service._resume_run

    async def crash_after_create(*args, **kwargs):
        raise RuntimeError("crash_after_create")

    monkeypatch.setattr(service, "_resume_run", crash_after_create)
    with pytest.raises(RuntimeError, match="crash_after_create"):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="drawing.cleanup-audit",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=_cleanup_inputs(),
            idempotency_key="crash-after-create",
            scopes=("autocad.read",),
        )
    run = (await service.repository.list_nonterminal_runs())[0]
    steps = await service.repository.list_steps("owner-a", run["run_id"])
    assert run["state"] == "running"
    assert next(step for step in steps if step["step_id"] == "query")[
        "state"
    ] == "ready"

    monkeypatch.setattr(service, "_resume_run", original)
    replay = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="crash-after-create",
        scopes=("autocad.read",),
    )
    assert replay["replayed"] is True
    assert replay["state"] == "waiting_for_user"


@pytest.mark.asyncio
async def test_startup_resumes_first_ready_step_without_an_action(
    service, monkeypatch
):
    original = service._resume_run

    async def crash_after_create(*args, **kwargs):
        raise RuntimeError("crash_after_create")

    monkeypatch.setattr(service, "_resume_run", crash_after_create)
    with pytest.raises(RuntimeError):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="drawing.cleanup-audit",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=_cleanup_inputs(),
            idempotency_key="startup-first-ready",
            scopes=("autocad.read",),
        )

    class IdleRunner:
        async def reconcile_restart(self):
            return 0

    monkeypatch.setattr(service, "_resume_run", original)
    service.action_runner = IdleRunner()
    await service.reconcile_restart()
    run = (await service.repository.list_nonterminal_runs())[0]
    assert run["state"] == "waiting_for_user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step_id", "target"),
    [
        ("query", "running"),
        ("query", "succeeded"),
        ("pure", "ready"),
        ("pure", "running"),
        ("pure", "succeeded"),
        ("report", "ready"),
        ("report", "running"),
        ("report", "succeeded"),
        ("review", "ready"),
        ("review", "running"),
        ("review", "waiting"),
    ],
)
async def test_cleanup_replay_heals_each_partial_step(
    service, monkeypatch, step_id, target
):
    original = service.repository.transition_step
    crashed = False

    async def crash_after_transition(**kwargs):
        nonlocal crashed
        result = await original(**kwargs)
        if (
            not crashed
            and kwargs["step_id"] == step_id
            and kwargs["target"] == target
        ):
            crashed = True
            raise RuntimeError(f"crash_{step_id}_{target}")
        return result

    monkeypatch.setattr(
        service.repository, "transition_step", crash_after_transition
    )
    with pytest.raises(RuntimeError):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="drawing.cleanup-audit",
            version=None,
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=_cleanup_inputs(),
            idempotency_key=f"cleanup-{step_id}-{target}",
            scopes=("autocad.read",),
        )
    replay = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key=f"cleanup-{step_id}-{target}",
        scopes=("autocad.read",),
    )
    assert replay["state"] == "waiting_for_user"
    events = await service.repository.list_events(
        "owner-a", replay["run_id"], limit=100
    )
    assert sum(event["event_type"] == "wait_created" for event in events) == 1


@pytest.mark.asyncio
async def test_cleanup_restart_finishes_after_review_transition(service):
    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="drawing.cleanup-audit",
        version=None,
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_cleanup_inputs(),
        idempotency_key="cleanup-finish-crash",
        scopes=("autocad.read",),
    )
    wait = await service.repository.current_wait("owner-a", started["run_id"])
    await service.repository.resolve_wait(
        owner_subject="owner-a",
        run_id=started["run_id"],
        wait_id=wait["wait_id"],
        expected_state_version=started["state_version"],
        response_schema_digest=wait["response_schema_digest"],
        response={"decision": "continue"},
        idempotency_key="cleanup-finish-control",
    )
    review = next(
        step
        for step in await service.repository.list_steps(
            "owner-a", started["run_id"]
        )
        if step["step_id"] == "review"
    )
    await service.repository.transition_step(
        owner_subject="owner-a",
        run_id=started["run_id"],
        step_id="review",
        attempt=1,
        expected_state="waiting",
        expected_version=review["state_version"],
        target="succeeded",
        output_ref=review["output_ref"],
    )

    class IdleRunner:
        async def reconcile_restart(self):
            return 0

    service.action_runner = IdleRunner()
    await service.reconcile_restart()
    assert (
        await service.repository.get_run("owner-a", started["run_id"])
    )["state"] == "succeeded"


@pytest.mark.asyncio
async def test_write_replay_inserts_missing_first_preview_action(
    tmp_path, monkeypatch
):
    database = SqliteDatabase(tmp_path / "preview-replay.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    repository = Phase9Repository(database)

    class Port:
        async def dispatch(self, action_kind, payload, *, idempotency_key):
            if action_kind == "preview":
                return {
                    "pure": {},
                    "prepare": {},
                    "preview": {"preview_id": "preview-a"},
                }
            return {
                "state": "awaiting_approval",
                "admission_status": "approval_required",
                "intent_id": "intent-a",
            }

        async def reconcile(self, action_kind, child_ref, *, idempotency_key):
            return None

    value = WorkflowApplicationService(
        repository,
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.plate-hole-pattern"},
        device_resolver=_write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=lambda *args: None,
        commit_request_executor=lambda *args: None,
        action_runner=WorkflowRunner(repository, Port(), worker_id="worker"),
    )
    value.initialize_catalog()
    original = repository.insert_action
    crashed = False

    async def crash_before_preview(**kwargs):
        nonlocal crashed
        if kwargs["action_kind"] == "preview" and not crashed:
            crashed = True
            raise RuntimeError("crash_before_preview_insert")
        return await original(**kwargs)

    monkeypatch.setattr(repository, "insert_action", crash_before_preview)
    with pytest.raises(WorkflowServiceError, match="backend_error"):
        await value.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="mechanical.plate-hole-pattern",
            version="1.0.0",
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=_plate_inputs(),
            idempotency_key="preview-replay",
            scopes=("autocad.read", "autocad.write"),
        )
    replay = await value.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.plate-hole-pattern",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_plate_inputs(),
        idempotency_key="preview-replay",
        scopes=("autocad.read", "autocad.write"),
    )
    assert replay["replayed"] is True
    assert replay["state"] == "waiting_for_trusted_approval"
    actions = await repository.list_actions("owner-a", replay["run_id"])
    assert [action["action_kind"] for action in actions] == [
        "preview",
        "commit",
    ]
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase7_status", "expected_state", "expected_wait"),
    [
        (
            {
                "state": "awaiting_approval",
                "intent_id": "intent-a",
            },
            "waiting_for_trusted_approval",
            "trusted_approval",
        ),
        (
            {
                "state": "running",
                "intent_id": "intent-a",
                "job_id": "job-a",
            },
            "waiting_for_job",
            "job",
        ),
        (
            {
                "state": "succeeded",
                "intent_id": "intent-a",
                "job_id": "job-a",
                "receipt_id": "receipt-a",
            },
            "succeeded",
            None,
        ),
    ],
)
async def test_real_action_port_recovers_commit_from_waiting_for_recovery(
    tmp_path, phase7_status, expected_state, expected_wait
):
    database = SqliteDatabase(tmp_path / f"recovery-{expected_state}.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    repository = Phase9Repository(database)
    durable_intents = {}
    creations = 0

    async def preview(*args):
        return {
            "pure": {},
            "prepare": {},
            "preview": {"preview_id": "preview-a"},
        }

    async def commit(owner, preview_id, idempotency_key, scopes):
        nonlocal creations
        assert (owner, preview_id) == ("owner-a", "preview-a")
        if idempotency_key not in durable_intents:
            creations += 1
            durable_intents[idempotency_key] = {
                "state": "awaiting_approval",
                "admission_status": "approval_required",
                "intent_id": "intent-a",
            }
            raise RuntimeError("crash_after_intent_creation")
        return durable_intents[idempotency_key]

    async def commit_status(owner, intent_id):
        assert (owner, intent_id) == ("owner-a", "intent-a")
        return phase7_status

    async def reconcile_lookup(
        action_kind, child_ref, *, idempotency_key
    ):
        assert action_kind == "commit"
        assert isinstance(child_ref["payload"], dict)
        result = durable_intents.get(idempotency_key)
        return (
            {"state": "succeeded", "result": result}
            if result is not None
            else None
        )

    port = _Phase9ActionPort(
        preview, commit, catalog_repository, reconcile_lookup
    )
    value = WorkflowApplicationService(
        repository,
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.plate-hole-pattern"},
        device_resolver=_write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=preview,
        commit_request_executor=commit,
        action_runner=WorkflowRunner(repository, port, worker_id="worker"),
        commit_status_resolver=commit_status,
    )
    value.initialize_catalog()
    started = await value.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.plate-hole-pattern",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_plate_inputs(),
        idempotency_key=f"recover-{expected_state}",
        scopes=("autocad.read", "autocad.write"),
    )
    assert started["state"] == "waiting_for_recovery"

    await value.reconcile_restart()
    first = await value.get("owner-a", started["run_id"])
    assert first["run"]["state"] == expected_state
    assert (
        first["current_wait"]["wait_kind"]
        if first["current_wait"] is not None
        else None
    ) == expected_wait
    commit_action = next(
        action
        for action in await repository.list_actions(
            "owner-a", started["run_id"]
        )
        if action["action_kind"] == "commit"
    )
    assert commit_action["child_ref"]["intent_id"] == "intent-a"
    action_count = len(
        await repository.list_actions("owner-a", started["run_id"])
    )
    event_count = len(
        await repository.list_events(
            "owner-a", started["run_id"], limit=100
        )
    )

    await value.reconcile_restart()
    second = await value.get("owner-a", started["run_id"])
    assert second["run"]["state"] == expected_state
    assert len(await repository.list_actions("owner-a", started["run_id"])) == action_count
    assert len(
        await repository.list_events(
            "owner-a", started["run_id"], limit=100
        )
    ) == event_count
    assert creations == 1
    await database.close()


@pytest.mark.asyncio
async def test_revoked_started_write_is_reconciled_once_without_redispatch(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "revoked-reconcile.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    repository = Phase9Repository(database)
    children = {}
    calls = {"dispatch": 0, "lookup": 0}

    async def preview(*args):
        return {
            "pure": {},
            "prepare": {},
            "preview": {"preview_id": "preview-a"},
        }

    async def commit(owner, preview_id, idempotency_key, scopes):
        assert (owner, preview_id) == ("owner-a", "preview-a")
        calls["dispatch"] += 1
        child_key = (
            "wf-"
            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
            + "-commit"
        )
        intent_id = DurableGatewayServices._phase7_stable_id(
            "intent", owner, child_key
        )
        children[intent_id] = {
            "state": "awaiting_approval",
            "intent_id": intent_id,
            "idempotency_key": child_key,
            "consent_id": None,
            "released_job_id": None,
        }
        raise RuntimeError("crash_after_intent")

    class _IntentRepository:
        async def get_intent(self, owner_subject, intent_id):
            assert owner_subject == "owner-a"
            return children.get(intent_id)

    lookup_service = object.__new__(DurableGatewayServices)
    lookup_service.phase7_repository = _IntentRepository()

    async def reconcile_lookup(action_kind, child_ref, *, idempotency_key):
        calls["lookup"] += 1
        return await lookup_service._phase9_reconcile_action(
            action_kind,
            child_ref,
            idempotency_key=idempotency_key,
        )

    port = _Phase9ActionPort(
        preview, commit, catalog_repository, reconcile_lookup
    )
    service = WorkflowApplicationService(
        repository,
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.plate-hole-pattern"},
        device_resolver=_write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=preview,
        commit_request_executor=commit,
        action_runner=WorkflowRunner(repository, port, worker_id="worker"),
    )
    service.initialize_catalog()
    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.plate-hole-pattern",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_plate_inputs(),
        idempotency_key="revoked-reconcile",
        scopes=("autocad.read", "autocad.write"),
    )
    assert started["state"] == "waiting_for_recovery"
    await repository.create_wait(
        owner_subject="owner-a",
        run_id=started["run_id"],
        step_id="commit",
        wait_kind="recovery",
        expected_state_version=started["state_version"],
        response_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    )
    catalog_repository.transition(
        "mechanical.plate-hole-pattern",
        "1.0.0",
        "published",
        "security_revoked",
        "security-operator",
    )

    await service.reconcile_restart()
    first = await service.get("owner-a", started["run_id"])
    assert first["run"]["state"] == "needs_attention"
    assert first["current_wait"] is None
    assert first["required_next_action"] is None
    action = next(
        item
        for item in await repository.list_actions("owner-a", started["run_id"])
        if item["action_kind"] == "commit"
    )
    assert action["state"] == "completed"
    assert action["result"]["intent_id"] in children
    event_count = len(
        await repository.list_events("owner-a", started["run_id"], limit=100)
    )

    await service.reconcile_restart()
    assert calls == {"dispatch": 1, "lookup": 1}
    assert len(children) == 1
    assert len(
        await repository.list_events("owner-a", started["run_id"], limit=100)
    ) == event_count
    await database.close()


@pytest.mark.asyncio
async def test_security_revocation_between_preview_and_commit_fails_closed(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "revoked.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    catalog_repository = SkillCatalogRepository(database)
    commit_calls = []

    async def preview(*args):
        return {
            "observe": {"snapshot_id": "snapshot-a"},
            "query": {"entities": []},
            "pure": {"semantic_digest": "sha256:" + "1" * 64},
            "prepare": {"program_id": "program-a", "program_revision": 1},
            "preview": {"preview_id": "preview-a", "state": "succeeded"},
        }

    async def commit(*args):
        commit_calls.append(args)
        return {}

    async def write_device(owner, device):
        return {
            "capabilities": {"cad.program.v1.compile"},
            "operation_packs": {"cad.program/1.0-create-core"},
            "runtime_release_verified": True,
            "capability_evidence_verified": True,
            "identity_generation": 1,
        }

    value = WorkflowApplicationService(
        Phase9Repository(database),
        catalog_repository,
        catalog,
        enabled=True,
        catalog_enabled=True,
        policy_epoch=9,
        write_enabled=True,
        enabled_skills={"mechanical.auto-dimension-overall"},
        device_resolver=write_device,
        snapshot_resolver=lambda owner, device, snapshot: _snapshot(
            owner, device, snapshot
        ),
        write_preview_executor=preview,
        commit_request_executor=commit,
    )
    value.initialize_catalog()
    started = await value.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs={
            "source_snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "layer": "DIM",
            "entity_ids": ["entity-a"],
            "profile": "mechanical_mm",
            "offset": 10.0,
        },
        idempotency_key="revocation-start",
        scopes=("autocad.read", "autocad.write"),
    )
    assert started["state"] == "waiting_for_user"
    catalog_repository.transition(
        "mechanical.auto-dimension-overall",
        "1.0.0",
        "published",
        "security_revoked",
        "security-operator",
    )
    with pytest.raises(WorkflowServiceError, match="skill_security_revoked"):
        await value.control(
            owner_subject="owner-a",
            run_id=started["run_id"],
            action="submit_input",
            expected_state_version=started["state_version"],
            idempotency_key="revoked-control",
            payload={"decision": "continue"},
            scopes=("autocad.read", "autocad.write"),
        )
    assert (
        await value.repository.get_run("owner-a", started["run_id"])
    )["state"] == "needs_attention"
    assert commit_calls == []
    await database.close()


@pytest.mark.asyncio
async def test_auto_dimension_restart_heals_missing_user_wait_once(
    tmp_path, monkeypatch
):
    database, service, _ = await _auto_dimension_service(tmp_path)
    original = service.repository.create_wait
    crashed = False

    async def crash_before_wait(**kwargs):
        nonlocal crashed
        if kwargs["wait_kind"] == "user_input" and not crashed:
            crashed = True
            raise RuntimeError("crash_before_user_wait")
        return await original(**kwargs)

    monkeypatch.setattr(service.repository, "create_wait", crash_before_wait)
    with pytest.raises(RuntimeError, match="crash_before_user_wait"):
        await service.start(
            owner_subject="owner-a",
            actor_subject="owner-a",
            skill_id="mechanical.auto-dimension-overall",
            version="1.0.0",
            device_id="device-a",
            source_snapshot_id="snapshot-a",
            inputs=_auto_dimension_inputs(),
            idempotency_key="missing-user-wait",
            scopes=("autocad.read", "autocad.write"),
        )
    run = (await service.repository.list_nonterminal_runs())[0]
    assert run["state"] == "waiting_for_user"
    assert await service.repository.current_wait("owner-a", run["run_id"]) is None

    await service.reconcile_restart()
    await service.reconcile_restart()
    detail = await service.get("owner-a", run["run_id"])
    assert detail["current_wait"]["wait_kind"] == "user_input"
    events = await service.repository.list_events(
        "owner-a", run["run_id"], limit=100
    )
    assert sum(event["event_type"] == "wait_created" for event in events) == 1
    await database.close()


@pytest.mark.asyncio
async def test_failed_preview_reaches_actionable_terminal_state(tmp_path):
    database, service, calls = await _auto_dimension_service(
        tmp_path, fail_preview=True
    )
    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_auto_dimension_inputs(),
        idempotency_key="failed-preview",
        scopes=("autocad.read", "autocad.write"),
    )
    assert started["state"] == "needs_attention"
    await service.maintenance_once()
    await service.maintenance_once()
    run = await service.repository.get_run("owner-a", started["run_id"])
    actions = await service.repository.list_actions("owner-a", started["run_id"])
    assert run["state"] == "needs_attention"
    assert actions[0]["state"] == "failed"
    assert calls == {"preview": 1, "commit": 0}
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_window",
    [
        "after_command_started",
        "after_resolve_wait",
        "after_review_succeeded",
        "after_run_commit",
        "before_commit_action",
        "after_commit_action_insert",
    ],
)
async def test_auto_dimension_submit_input_recovers_each_crash_window(
    tmp_path, monkeypatch, crash_window
):
    database, service, calls = await _auto_dimension_service(tmp_path)
    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_auto_dimension_inputs(),
        idempotency_key=f"submit-{crash_window}",
        scopes=("autocad.read", "autocad.write"),
    )
    crashed = False
    original_begin = service.repository.begin_control_command
    original_resolve = service.repository.resolve_wait
    original_step = service.repository.transition_step
    original_run = service.repository.transition_run
    original_insert = service.repository.insert_action
    original_run_once = service.action_runner.run_once

    async def begin_control_command(**kwargs):
        nonlocal crashed
        result = await original_begin(**kwargs)
        if crash_window == "after_command_started" and not crashed:
            crashed = True
            raise RuntimeError(crash_window)
        return result

    async def resolve_wait(**kwargs):
        nonlocal crashed
        result = await original_resolve(**kwargs)
        if crash_window == "after_resolve_wait" and not crashed:
            crashed = True
            raise RuntimeError(crash_window)
        return result

    async def transition_step(**kwargs):
        nonlocal crashed
        result = await original_step(**kwargs)
        if (
            crash_window == "after_review_succeeded"
            and kwargs["step_id"] == "review"
            and kwargs["target"] == "succeeded"
            and not crashed
        ):
            crashed = True
            raise RuntimeError(crash_window)
        return result

    async def transition_run(**kwargs):
        nonlocal crashed
        result = await original_run(**kwargs)
        if (
            crash_window == "after_run_commit"
            and kwargs["target"] == "running"
            and kwargs["current_step_id"] == "commit"
            and not crashed
        ):
            crashed = True
            raise RuntimeError(crash_window)
        return result

    async def insert_action(**kwargs):
        nonlocal crashed
        if (
            crash_window == "before_commit_action"
            and kwargs["action_kind"] == "commit"
            and not crashed
        ):
            crashed = True
            raise RuntimeError(crash_window)
        return await original_insert(**kwargs)

    async def run_once():
        nonlocal crashed
        if crash_window == "after_commit_action_insert" and not crashed:
            crashed = True
            raise RuntimeError(crash_window)
        return await original_run_once()

    monkeypatch.setattr(
        service.repository, "begin_control_command", begin_control_command
    )
    monkeypatch.setattr(service.repository, "resolve_wait", resolve_wait)
    monkeypatch.setattr(service.repository, "transition_step", transition_step)
    monkeypatch.setattr(service.repository, "transition_run", transition_run)
    monkeypatch.setattr(service.repository, "insert_action", insert_action)
    monkeypatch.setattr(service.action_runner, "run_once", run_once)
    control = {
        "owner_subject": "owner-a",
        "run_id": started["run_id"],
        "action": "submit_input",
        "expected_state_version": started["state_version"],
        "idempotency_key": f"control-{crash_window}",
        "payload": {"decision": "continue"},
        "scopes": ("autocad.read", "autocad.write"),
    }
    with pytest.raises((RuntimeError, WorkflowServiceError)):
        await service.control(**control)

    await service.reconcile_restart()
    recovered = await service.control(**control)
    assert recovered["state"] == "waiting_for_trusted_approval"
    assert await service.control(**control) == recovered
    await service.reconcile_restart()
    event_count = len(
        await service.repository.list_events(
            "owner-a", started["run_id"], limit=100
        )
    )
    await service.reconcile_restart()
    assert len(
        await service.repository.list_events(
            "owner-a", started["run_id"], limit=100
        )
    ) == event_count
    actions = await service.repository.list_actions(
        "owner-a", started["run_id"]
    )
    assert [action["action_kind"] for action in actions] == ["preview", "commit"]
    assert calls == {"preview": 1, "commit": 1}
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step_id", "target", "before"),
    [
        ("commit", "succeeded", False),
        (None, "running", False),
        ("validate", "ready", False),
        ("validate", "running", False),
        ("validate", "succeeded", False),
        ("finish", "ready", False),
        ("finish", "running", False),
        ("finish", "succeeded", False),
        (None, "succeeded", True),
    ],
)
async def test_commit_success_resumes_validate_and_finish_from_each_crash_window(
    tmp_path, monkeypatch, step_id, target, before
):
    async def commit_status(owner, intent_id):
        assert (owner, intent_id) == ("owner-a", "intent-a")
        return {
            "state": "succeeded",
            "intent_id": "intent-a",
            "job_id": "job-a",
            "receipt_id": "receipt-a",
        }

    database, service, calls = await _auto_dimension_service(
        tmp_path, commit_status=commit_status
    )
    started = await service.start(
        owner_subject="owner-a",
        actor_subject="owner-a",
        skill_id="mechanical.auto-dimension-overall",
        version="1.0.0",
        device_id="device-a",
        source_snapshot_id="snapshot-a",
        inputs=_auto_dimension_inputs(),
        idempotency_key=f"finish-{step_id}-{target}-{before}",
        scopes=("autocad.read", "autocad.write"),
    )
    waiting = await service.control(
        owner_subject="owner-a",
        run_id=started["run_id"],
        action="submit_input",
        expected_state_version=started["state_version"],
        idempotency_key=f"finish-control-{step_id}-{target}-{before}",
        payload={"decision": "continue"},
        scopes=("autocad.read", "autocad.write"),
    )
    assert waiting["state"] == "waiting_for_trusted_approval"
    original_step = service.repository.transition_step
    original_run = service.repository.transition_run
    crashed = False

    async def transition_step(**kwargs):
        nonlocal crashed
        result = await original_step(**kwargs)
        if (
            kwargs["step_id"] == step_id
            and kwargs["target"] == target
            and not crashed
        ):
            crashed = True
            raise RuntimeError("finish_crash")
        return result

    async def transition_run(**kwargs):
        nonlocal crashed
        matches_validate = (
            step_id is None
            and target == "running"
            and kwargs["target"] == "running"
            and kwargs["current_step_id"] == "validate"
        )
        matches_terminal = (
            step_id is None
            and target == "succeeded"
            and kwargs["target"] == "succeeded"
        )
        if before and matches_terminal and not crashed:
            crashed = True
            raise RuntimeError("finish_crash")
        result = await original_run(**kwargs)
        if not before and matches_validate and not crashed:
            crashed = True
            raise RuntimeError("finish_crash")
        return result

    monkeypatch.setattr(service.repository, "transition_step", transition_step)
    monkeypatch.setattr(service.repository, "transition_run", transition_run)
    with pytest.raises(RuntimeError, match="finish_crash"):
        await service.reconcile_restart()

    await service.reconcile_restart()
    detail = await service.get("owner-a", started["run_id"])
    assert detail["run"]["state"] == "succeeded"
    assert calls == {"preview": 1, "commit": 1}
    event_count = len(
        await service.repository.list_events(
            "owner-a", started["run_id"], limit=100
        )
    )
    await service.reconcile_restart()
    assert len(
        await service.repository.list_events(
            "owner-a", started["run_id"], limit=100
        )
    ) == event_count
    await database.close()
