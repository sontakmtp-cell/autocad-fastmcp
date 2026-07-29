"""Phase 3 durable application facade used by the thin FastMCP layer."""

from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from autocad_contracts import (
    Phase8ApprovalBinding,
    ProgramPreviewResult,
    build_execution_binding_v1,
    normalize_sha256_digest,
)
from cad_core.phase9_workflows import (
    plan_auto_dimension_overall,
    render_plate_hole_pattern,
)

from .application.job_service import DurableJobError, DurableJobService
from .domain.jobs import InvalidJobTransition
from .contracts import (
    CadEntity,
    CadGetJobInput,
    CadGetJobOutput,
    CadGetJobOutputC1,
    CadJobEvent,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadListDevicesOutputC1,
    CadObserveInput,
    CadObserveInputDurable,
    CadObserveOutputDurable,
    CadObserveOutputC1,
    CadQueryInput,
    CadQueryOutput,
    CadPrepareProgramV1ConflictOutput,
    CadPrepareProgramInput,
    CadPrepareProgramV1Output,
    CadPrepareProgramV1RevisionRequest,
    CadPreviewOutput,
    CadPreviewInput,
    CadCommitInput,
    DeviceInfo,
    DeviceInfoC1,
    ExecutionEvidence,
    PackageEvidence,
    PHASE3_CONTRACT_VERSION,
    PHASE4_CONTRACT_VERSION,
    Principal,
    RevisionEvidence,
)
from .services import GatewayError
from .snapshots import canonical_json, cursor_filter_hash, decode_cursor, encode_cursor
from .infrastructure.agent_transport.connection_registry import ConnectionRegistry
from .infrastructure.agent_transport.authenticator import FixtureDeviceAuthenticator
from .infrastructure.sqlite.database import DatabaseError, SqliteDatabase
from .infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository
from .infrastructure.sqlite.program_repository import ProgramRepository
from .program_services import ProgramGatewayPolicy, ProgramGatewayService
from .phase7_admission import Phase7AdmissionPolicy, Phase7AdmissionService
from .phase7_recovery import Phase7RecoveryService
from .infrastructure.sqlite.phase7_repository import Phase7Repository
from .infrastructure.sqlite.phase8_repository import Phase8Repository
from .infrastructure.sqlite.phase9_repository import Phase9Repository
from .skills.catalog import SkillCatalog
from .skills.catalog_repository import SkillCatalogRepository
from .workflows.service import WorkflowApplicationService
from .workflows.runner import WorkflowRunner
from .phase8_gateway import Phase8FeatureFlags, Phase8GatewayService
from .phase8_contract_adapter import (
    COMPILER_CORE_OPERATION_PACK,
    CREATE_EQUIVALENT_OPERATION_PACK,
    Phase8CompilerPort,
    Phase8RevisionPort,
)


PHASE3_OWNER = "phase3-fixture-user"
PHASE3_CAPABILITIES = ["observe", "query"]
logger = logging.getLogger(__name__)


class _Phase9ActionPort:
    def __init__(
        self,
        preview: Any,
        commit: Any,
        catalog_repository: Any,
        reconcile_lookup: Any | None = None,
    ) -> None:
        self.preview = preview
        self.commit = commit
        self.catalog_repository = catalog_repository
        self.reconcile_lookup = reconcile_lookup

    async def preflight(
        self, action_kind: str, payload: dict[str, Any]
    ) -> None:
        del action_kind
        status = self.catalog_repository.get_status(
            payload["skill_id"], payload["skill_version"]
        )
        if status in {"security_revoked", "withdrawn"}:
            raise GatewayError(
                "skill_security_revoked"
                if status == "security_revoked"
                else "skill_withdrawn"
            )

    async def dispatch(
        self, action_kind: str, payload: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        await self.preflight(action_kind, payload)
        if action_kind == "preview":
            return await self.preview(
                payload["owner_subject"],
                payload["skill_id"],
                payload["device_id"],
                payload["snapshot_id"],
                payload["inputs"],
                idempotency_key,
                tuple(payload["scopes"]),
            )
        if action_kind == "commit":
            return await self.commit(
                payload["owner_subject"],
                payload["preview_id"],
                idempotency_key,
                tuple(payload["scopes"]),
            )
        raise GatewayError("workflow_action_invalid")

    async def reconcile(
        self,
        action_kind: str,
        child_ref: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        if self.reconcile_lookup is None:
            return None
        return await self.reconcile_lookup(
            action_kind,
            child_ref,
            idempotency_key=idempotency_key,
        )

_SAFE_JOB_ERROR_CODES = frozenset(
    {
        "agent_rejected",
        "active_document_changed",
        "autocad_busy",
        "autocad_not_running",
        "backend_error",
        "capability_missing",
        "command_routing_failed",
        "deadline_expired",
        "device_offline",
        "dispatcher_not_loaded",
        "dispatcher_timeout",
        "idempotency_conflict",
        "ipc_result_invalid",
        "modal_dialog_active",
        "no_active_document",
        "payload_mismatch",
        "package_mismatch",
        "paused_by_user",
        "binding_mismatch",
        "document_write_busy",
        "feature_disabled",
        "policy_mismatch",
        "preview_expired",
        "runtime_mismatch",
        "stale_revision",
        "stale_snapshot",
        "write_lock_disabled",
    }
)


class DurableGatewayServices:
    """SQLite truth plus in-memory socket presence for one POC Gateway worker."""

    is_phase3 = True

    def __init__(
        self,
        database: SqliteDatabase,
        registry: ConnectionRegistry,
        *,
        device_tokens: dict[str, str],
        owner_subject: str = PHASE3_OWNER,
        request_wait_timeout_seconds: float = 30,
        job_deadline_seconds: float = 300,
        maintenance_interval_seconds: float | None = None,
        profile: str = "phase3_poc",
        agent_authenticator: Any | None = None,
        required_package: dict[str, str] | None = None,
        display_name: str | None = None,
        program_enabled: bool = False,
        managed_write_enabled: bool = False,
        allowed_write_device_ids: tuple[str, ...] = (),
        program_policy_version: str = "phase6-policy/1",
        phase7_c2_enabled: bool = False,
        trusted_approval_enabled: bool = False,
        device_local_approval_enabled: bool = False,
        portal_recent_auth_approval_enabled: bool = False,
        public_rollback_enabled: bool = False,
        recovery_cases_enabled: bool = False,
        phase6_direct_commit_lab_enabled: bool = False,
        phase8_feature_flags: Phase8FeatureFlags | None = None,
        phase8_compiler: Phase8CompilerPort | None = None,
        phase8_revision_adapter: Phase8RevisionPort | None = None,
        phase9_enabled: bool = False,
        phase9_catalog_enabled: bool = False,
        phase9_public_tools_enabled: bool = False,
        phase9_write_enabled: bool = False,
        phase9_policy_epoch: int = 0,
        phase9_catalog_root: str | None = None,
        phase9_skill_allowlist: tuple[str, ...] = (),
        phase9_enabled_skills: tuple[str, ...] = (),
    ) -> None:
        self.database = database
        self.registry = registry
        self.repository = SqliteRepository(database)
        self.is_phase7 = profile in {"phase7_c2", "phase8_program", "phase9_workflow"}
        self.is_phase6 = profile in {
            "phase6_program",
            "phase7_c2",
            "phase8_program",
            "phase9_workflow",
        }
        self.is_phase8 = profile in {"phase8_program", "phase9_workflow"}
        self.is_phase9 = profile == "phase9_workflow"
        self.program_repository = ProgramRepository(database) if self.is_phase6 else None
        self.phase7_repository = Phase7Repository(database) if self.is_phase7 else None
        self.phase8_repository = Phase8Repository(database) if self.is_phase6 else None
        self.phase9_repository = Phase9Repository(database) if self.is_phase9 else None
        self.phase8_gateway = (
            Phase8GatewayService(
                self.phase8_repository,
                phase8_feature_flags or Phase8FeatureFlags(),
                compiler=phase8_compiler,
                revision_adapter=phase8_revision_adapter,
            )
            if self.phase8_repository is not None
            else None
        )
        self.phase7_recovery = (
            Phase7RecoveryService(
                self.repository,
                self.phase7_repository,
                cases_enabled=recovery_cases_enabled,
            )
            if self.phase7_repository is not None
            else None
        )
        self.job_service = DurableJobService(
            self.repository,
            registry,
            request_wait_timeout_seconds=request_wait_timeout_seconds,
            required_package=required_package,
            program_repository=self.program_repository,
            program_policy_version=(
                program_policy_version if self.is_phase6 else None
            ),
            managed_write_enabled=managed_write_enabled,
            allowed_write_device_ids=allowed_write_device_ids,
            phase7_recovery_service=self.phase7_recovery,
        )
        self.device_tokens = dict(device_tokens)
        self.agent_authenticator = agent_authenticator
        if self.agent_authenticator is None and profile not in {
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
            "phase8_program",
            "phase9_workflow",
        }:
            self.agent_authenticator = FixtureDeviceAuthenticator(self.device_tokens)
        self.owner_subject = owner_subject
        self.profile = profile
        self.is_phase4 = profile in {
            "phase4_c1",
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
            "phase8_program",
            "phase9_workflow",
        }
        self.is_phase5_identity = profile in {
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
            "phase8_program",
            "phase9_workflow",
        }
        self.required_package = dict(required_package or {})
        self.display_name = display_name
        self.job_deadline_seconds = max(1.0, min(float(job_deadline_seconds), 86_400.0))
        self.maintenance_interval_seconds = maintenance_interval_seconds
        self._initialized = False
        self._maintenance_task: asyncio.Task[None] | None = None
        self._maintenance_error: BaseException | None = None
        self.program_service = (
            ProgramGatewayService(
                self.repository,
                self.program_repository,
                registry,
                self.job_service,
                ProgramGatewayPolicy(
                    program_enabled=program_enabled,
                    managed_write_enabled=managed_write_enabled,
                    allowed_device_ids=allowed_write_device_ids,
                    policy_version=program_policy_version,
                    job_deadline_seconds=self.job_deadline_seconds,
                ),
            )
            if self.program_repository is not None
            else None
        )
        self.phase7_admission = (
            Phase7AdmissionService(
                self.program_service,
                self.phase7_repository,
                Phase7AdmissionPolicy(
                    phase7_c2_enabled=phase7_c2_enabled,
                    trusted_approval_enabled=trusted_approval_enabled,
                    device_local_approval_enabled=device_local_approval_enabled,
                    portal_recent_auth_approval_enabled=(
                        portal_recent_auth_approval_enabled
                    ),
                    public_rollback_enabled=public_rollback_enabled,
                    recovery_cases_enabled=recovery_cases_enabled,
                    phase6_direct_commit_lab_enabled=phase6_direct_commit_lab_enabled,
                    profile=profile,
                    policy_version=program_policy_version,
                    job_deadline_seconds=self.job_deadline_seconds,
                ),
            )
            if self.phase7_repository is not None and self.program_service is not None
            else None
        )
        if self.phase7_admission is not None:
            self.phase7_admission.rollback_preview_provider = (
                self._phase7_rollback_preview_provider
            )
            self.phase7_admission.phase8_commit_provider = (
                self._phase8_commit_payload_provider
                if self.is_phase8
                else None
            )
        self.phase6_direct_commit_lab_enabled = phase6_direct_commit_lab_enabled
        self.workflow_service = None
        if self.is_phase9 and phase9_catalog_root:
            catalog = SkillCatalog.from_fixed_package_root(Path(phase9_catalog_root))
            phase9_catalog_repository = SkillCatalogRepository(database)
            phase9_runner = WorkflowRunner(
                self.phase9_repository,
                _Phase9ActionPort(
                    self._phase9_write_preview,
                    self._phase9_commit_request,
                    phase9_catalog_repository,
                    self._phase9_reconcile_action,
                ),
                worker_id="phase9-gateway",
            )
            self.workflow_service = WorkflowApplicationService(
                self.phase9_repository, phase9_catalog_repository, catalog,
                enabled=phase9_enabled and phase9_public_tools_enabled, catalog_enabled=phase9_catalog_enabled,
                policy_epoch=phase9_policy_epoch, write_enabled=phase9_write_enabled,
                allowlist=set(phase9_skill_allowlist),
                enabled_skills=set(phase9_enabled_skills),
                device_resolver=self._phase9_device_context,
                snapshot_resolver=self._phase9_snapshot,
                write_preview_executor=self._phase9_write_preview,
                commit_request_executor=self._phase9_commit_request,
                action_runner=phase9_runner,
                commit_status_resolver=self._phase9_commit_status,
            )

    async def _phase7_rollback_preview_provider(
        self,
        checkpoint: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            self.program_repository is None
            or self.phase7_admission is None
            or not self.phase7_admission.policy.public_rollback_enabled
        ):
            raise GatewayError("feature_disabled")
        program = await self.program_repository.get_program_revision(
            checkpoint["owner_subject"],
            checkpoint["program_id"],
            checkpoint["program_revision"],
        )
        if program is None:
            raise GatewayError("not_found")
        plan_view = {
            "rollback_execution_digest": request["rollback_execution_digest"],
            "current_document_revision": checkpoint["document_revision_after"],
            "runtime_pins": checkpoint["runtime_pins"],
            "policy_pins": checkpoint["policy_pins"],
        }
        payload = {
            "kind": "rollback_preview",
            "effect_class": "read",
            "binding": self.phase7_admission._rollback_binding(
                checkpoint, plan_view
            ),
            "arguments": {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "checkpoint_digest": checkpoint["checkpoint_digest"],
                "rollback_plan_id": request["plan_id"],
                "rollback_execution_digest": request[
                    "rollback_execution_digest"
                ],
                "expires_at": request["expires_at"],
            },
        }
        idempotency_key = (
            "phase7-preview-"
            + hashlib.sha256(
                (
                    request["plan_id"]
                    + "\0"
                    + request["attempt_id"]
                ).encode("utf-8")
            ).hexdigest()[:48]
        )
        try:
            job = await self.repository.create_job(
                owner_subject=checkpoint["owner_subject"],
                device_id=program["device_id"],
                kind="rollback_preview",
                effect_class="read",
                payload=payload,
                idempotency_key=idempotency_key,
                deadline_at=request["expires_at"],
            )
            completed = await self.job_service.wait_for_existing_job(
                job,
                owner_subject=checkpoint["owner_subject"],
                correlation_id=request["plan_id"],
            )
        except (RepositoryConflict, DurableJobError) as error:
            raise GatewayError(getattr(error, "code", "backend_error")) from None
        if completed["state"] != "succeeded" or not isinstance(
            completed.get("result"), dict
        ):
            raise GatewayError(completed.get("error_code") or "invalid_response")
        result = dict(completed["result"])
        expected_host_runtime = {
            "runtime_id": checkpoint["runtime_pins"]["runtime_id"],
            "runtime_role": checkpoint["runtime_pins"]["runtime_role"],
            "host_family": checkpoint["runtime_pins"]["host_family"],
            "host_version": checkpoint["runtime_pins"]["host_version"],
            "host_package_id": checkpoint["runtime_pins"]["host_package_id"],
            "host_package_version": checkpoint["runtime_pins"][
                "host_package_version"
            ],
            "host_package_hash": checkpoint["runtime_pins"]["host_package_hash"],
        }
        if (
            result.get("runtime_pins") != expected_host_runtime
            or result.get("policy_pins") != checkpoint["policy_pins"]
        ):
            raise GatewayError("binding_mismatch")
        result["runtime_pins"] = checkpoint["runtime_pins"]
        result["policy_pins"] = checkpoint["policy_pins"]
        return result

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.database.open()
        if self.workflow_service is not None:
            self.workflow_service.initialize_catalog()
            await self.workflow_service.reconcile_restart()
        await self.repository.mark_sessions_disconnected()
        for device_id in self.device_tokens:
            await self.repository.seed_device(
                owner_subject=self.owner_subject,
                device_id=device_id,
                display_name=self.display_name or f"Simulated {device_id}",
                capabilities=["observe"] if self.is_phase4 else PHASE3_CAPABILITIES,
                fixture_auth_ref=(f"lab:{device_id}" if self.is_phase4 else f"fixture:{device_id}"),
            )
        for job in await self.repository.all_nonterminal_jobs():
            if job["state"] in {"dispatched", "acknowledged", "running", "cancel_requested"}:
                target = (
                    "outcome_unknown"
                    if job["effect_class"] == "write" and job["state"] in {"acknowledged", "running", "cancel_requested"}
                    else "reconnect_pending"
                )
                try:
                    await self.repository.transition_job(job["job_id"], target)
                except (RepositoryConflict, InvalidJobTransition):
                    logger.info(
                        "Startup recovery lost a state race",
                        extra={"job_id": job["job_id"], "state": job["state"]},
                    )
        self._initialized = True
        self._maintenance_error = None
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        self._maintenance_task.add_done_callback(self._maintenance_done)

    async def _phase9_device_context(
        self, owner_subject: str, device_id: str
    ) -> dict[str, Any]:
        device = await self._require_device(
            device_id,
            Principal(subject=owner_subject, scopes=("autocad.read",)),
        )
        connection = await self.registry.get(device_id)
        fresh = bool(
            connection is not None
            and await self.registry.is_current_and_fresh(connection)
        )
        capabilities = set(device.get("capabilities", ()))
        if fresh and self.is_phase8:
            capabilities.add("cad.program.v1.compile")
        operation_packs = (
            set(self.phase8_gateway.flags.operation_pack_allowlist)
            if fresh and self.phase8_gateway is not None
            else set()
        )
        if (
            fresh
            and self.phase8_gateway is not None
            and device_id in self.program_service.allowed_device_ids
            and self.phase8_gateway.flags.source_enabled
            and self.phase8_gateway.flags.compiler_enabled
            and self.phase8_gateway.flags.create_pack_enabled
            and {
                COMPILER_CORE_OPERATION_PACK,
                CREATE_EQUIVALENT_OPERATION_PACK,
            }.issubset(operation_packs)
        ):
            operation_packs.add("cad.program/1.0-create-core")
        return {
            "capabilities": capabilities,
            "operation_packs": operation_packs,
            "runtime_release_verified": fresh and self.is_phase8,
            "capability_evidence_verified": fresh,
            "identity_generation": 1,
        }

    async def _phase9_snapshot(
        self, owner_subject: str, device_id: str, snapshot_id: str
    ) -> dict[str, Any]:
        snapshot = await self.repository.get_snapshot(owner_subject, snapshot_id)
        if snapshot is None or snapshot.get("device_id") != device_id:
            raise GatewayError("not_found")
        value = dict(snapshot)
        value["document_id"] = self.program_service._snapshot_document_id(snapshot)
        return value

    async def _phase9_write_preview(
        self,
        owner_subject: str,
        skill_id: str,
        device_id: str,
        snapshot_id: str,
        inputs: dict[str, Any],
        idempotency_key: str,
        scopes: tuple[str, ...],
    ) -> dict[str, Any]:
        snapshot = await self._phase9_snapshot(
            owner_subject, device_id, snapshot_id
        )
        run_key = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        context = {
            "run_id": run_key,
            "device_id": device_id,
            "source_snapshot_id": snapshot_id,
            "document_id": snapshot["document_id"],
            "expected_document_revision": snapshot["document_revision"],
        }
        entities = snapshot.get("entities") or []
        if skill_id == "mechanical.auto-dimension-overall":
            requested = set(inputs["entity_ids"])
            selected = [
                entity
                for entity in entities
                if entity.get("entity_id") in requested
            ]
            if {entity.get("entity_id") for entity in selected} != requested:
                raise GatewayError("not_found")
            program = plan_auto_dimension_overall(
                context,
                selected,
                {
                    "profile": inputs["profile"],
                    "offset": inputs["offset"],
                    "target_layer": inputs["layer"],
                },
            )
            observe_result = {
                "snapshot_id": snapshot_id,
                "document_revision": snapshot["document_revision"],
            }
            query_result = {
                "entity_ids": sorted(requested),
                "entity_count": len(selected),
            }
        elif skill_id == "mechanical.plate-hole-pattern":
            program = render_plate_hole_pattern(context, inputs)
            observe_result = {}
            query_result = {}
        else:
            raise GatewayError("not_found")
        principal = Principal(subject=owner_subject, scopes=scopes)
        prepare_request = CadPrepareProgramInput(
            device_id=device_id,
            source_snapshot_id=snapshot_id,
            operations=program["operations"],
            idempotency_key=f"wf-{run_key}-prepare",
        )
        prepared = await self.prepare_program(
            prepare_request,
            principal,
            f"workflow-{run_key}-prepare",
            schema_version="cad.program/1.0",
            program_v1_source=program,
        )
        preview = await self.preview_program(
            CadPreviewInput(
                program_id=prepared.program_id,
                program_revision=prepared.program_revision,
                idempotency_key=f"wf-{run_key}-preview",
            ),
            principal,
            f"workflow-{run_key}-preview",
        )
        preview_job = await self.repository.get_job(
            owner_subject, preview.job_id
        )
        if preview_job is None:
            raise GatewayError("preview_unavailable")
        try:
            completed_preview = await self.job_service.wait_for_existing_job(
                preview_job,
                owner_subject=owner_subject,
                correlation_id=f"workflow-{run_key}-preview",
            )
        except DurableJobError as error:
            raise GatewayError(
                self._safe_job_error_code(error.code)
            ) from None
        if completed_preview["state"] != "succeeded" or not isinstance(
            completed_preview.get("result"), dict
        ):
            raise GatewayError(
                completed_preview.get("error_code") or "job_in_progress"
            )
        preview_value = preview.model_dump(mode="json")
        preview_value["state"] = "succeeded"
        preview_value["validation"] = completed_preview["result"]
        return {
            "observe": observe_result,
            "query": query_result,
            "pure": program,
            "prepare": prepared.model_dump(mode="json"),
            "preview": preview_value,
        }

    async def _phase9_commit_request(
        self,
        owner_subject: str,
        preview_id: str,
        idempotency_key: str,
        scopes: tuple[str, ...],
    ) -> dict[str, Any]:
        key = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        try:
            value = await self.commit_program(
                CadCommitInput(
                    preview_id=preview_id,
                    idempotency_key=f"wf-{key}-commit",
                ),
                Principal(subject=owner_subject, scopes=scopes),
                f"workflow-{key}-commit",
            )
        except GatewayError:
            logger.exception("Phase 9 commit request failed")
            raise
        result = value.model_dump(mode="json")
        if result.get("admission_status") not in {
            "approval_required",
            "released",
            "current_job",
            "receipt",
        }:
            raise GatewayError("backend_error")
        return result

    async def _phase9_reconcile_action(
        self,
        action_kind: str,
        child_ref: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        payload = child_ref.get("payload")
        if (
            action_kind != "commit"
            or not isinstance(payload, dict)
            or self.phase7_repository is None
        ):
            return None
        owner_subject = payload.get("owner_subject")
        if not isinstance(owner_subject, str):
            return None
        child_key = (
            "wf-"
            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
            + "-commit"
        )
        intent_id = child_ref.get("intent_id") or self._phase7_stable_id(
            "intent", owner_subject, child_key
        )
        intent = await self.phase7_repository.get_intent(
            owner_subject, str(intent_id)
        )
        if intent is None or intent.get("idempotency_key") != child_key:
            return None
        result = {
            "state": intent["state"],
            "admission_status": (
                "current_job"
                if intent["state"] == "released"
                else "approval_required"
            ),
            "intent_id": intent["intent_id"],
            "consent_id": intent.get("consent_id"),
            "job_id": intent.get("released_job_id"),
        }
        return {"state": "succeeded", "result": result}

    async def _phase9_commit_status(
        self, owner_subject: str, intent_id: str
    ) -> dict[str, Any]:
        if self.phase7_repository is None:
            raise GatewayError("feature_disabled")
        intent = await self.phase7_repository.get_intent(
            owner_subject, intent_id
        )
        if intent is None:
            raise GatewayError("not_found")
        if intent["state"] != "released":
            return {"state": intent["state"], "intent_id": intent_id}
        job_id = intent.get("released_job_id")
        if not isinstance(job_id, str):
            return {"state": "outcome_unknown", "intent_id": intent_id}
        job = await self.repository.get_job(owner_subject, job_id)
        if job is None:
            return {"state": "outcome_unknown", "intent_id": intent_id}
        result = {
            "state": job["state"],
            "intent_id": intent_id,
            "job_id": job_id,
        }
        if job["state"] == "succeeded" and self.program_repository is not None:
            binding = job.get("payload", {}).get("binding")
            if (
                isinstance(binding, dict)
                and binding.get("schema_version")
                == "cad.execution-binding/1"
                and isinstance(job.get("result"), dict)
                and isinstance(job["result"].get("receipt_id"), str)
            ):
                return {
                    **result,
                    "receipt_id": job["result"]["receipt_id"],
                    "receipt": job["result"],
                }
            receipt = await self.program_repository.get_receipt_by_job(
                owner_subject, job_id
            )
            if receipt is None:
                return {**result, "state": "outcome_unknown"}
            result.update(
                {
                    "receipt_id": receipt["receipt_id"],
                    "receipt": receipt,
                }
            )
        return result

    async def commit_program(
        self, request: Any, principal: Principal, correlation_id: str
    ) -> Any:
        if getattr(self, "is_phase8", False) and getattr(
            self, "phase8_repository", None
        ) is not None:
            preview = await self.phase8_repository.get_preview(
                principal.subject, request.preview_id
            )
            if preview is not None:
                if (
                    self.phase7_admission is None
                    or self.phase8_gateway is None
                ):
                    raise GatewayError("feature_disabled")
                plan = await self.phase8_repository.get_plan(
                    principal.subject, preview["plan_id"]
                )
                if plan is None:
                    raise GatewayError("not_found")
                preview_result = await self._phase8_preview_result(
                    principal.subject, preview
                )
                preview = {
                    **preview,
                    "preview_digest": preview_result.preview_digest,
                }
                connection, current_pins = (
                    await self._phase8_current_binding(plan)
                )
                if (
                    connection.hard_pause
                    or connection.paused
                    or not connection.write_lock_enabled
                ):
                    raise GatewayError("write_lock_disabled")
                await self._phase8_admit(
                    principal=principal,
                    plan=plan,
                    connection=connection,
                    current_pins=current_pins,
                    action="commit",
                )
                return await self.phase7_admission.create_phase8_commit_intent(
                    request=request,
                    principal=principal,
                    plan=plan,
                    preview=preview,
                    connection=connection,
                    phase8_gateway=self.phase8_gateway,
                    correlation_id=correlation_id,
                )
        if self.phase7_admission is not None:
            return await self.phase7_admission.commit(
                request, principal, correlation_id
            )
        if not self.phase6_direct_commit_lab_enabled:
            raise GatewayError("feature_disabled")
        return await self.program_service.commit(request, principal, correlation_id)

    async def _phase8_commit_payload_provider(
        self, intent: dict[str, Any]
    ) -> dict[str, Any]:
        if (
            self.phase8_repository is None
            or self.phase8_gateway is None
        ):
            raise GatewayError("feature_disabled")
        preview = await self.phase8_repository.get_preview(
            intent["owner_subject"], intent["preview_id"]
        )
        if preview is None:
            raise GatewayError("binding_mismatch")
        preview_result = await self._phase8_preview_result(
            intent["owner_subject"], preview
        )
        plan = await self.phase8_repository.get_plan(
            intent["owner_subject"], preview["plan_id"]
        )
        if plan is None:
            raise GatewayError("binding_mismatch")
        connection, current_pins = await self._phase8_current_binding(plan)
        if (
            connection.hard_pause
            or connection.paused
            or not connection.write_lock_enabled
        ):
            raise GatewayError("write_lock_disabled")
        admission = await self._phase8_admit(
            principal=Principal(
                subject=intent["owner_subject"], scopes=("autocad.write",)
            ),
            plan=plan,
            connection=connection,
            current_pins=current_pins,
            action="commit",
        )
        binding = build_execution_binding_v1(
            plan["plan"],
            action="commit",
            preview_id=preview["preview_id"],
            preview_expires_at=preview["expires_at"],
            receipt_id=intent["deterministic_receipt_id"],
        ).model_dump(mode="json")
        if (
            binding["execution_binding_digest"]
            != intent["commit_execution_digest"]
            or preview_result.preview_digest != intent["preview_digest"]
        ):
            raise GatewayError("binding_mismatch")
        consent = self._phase8_approved_consent(intent)
        job_id = self._phase7_stable_id("job", intent["intent_id"])
        command_id = self._phase7_stable_id(
            "command", intent["intent_id"]
        )
        release_key = self._phase7_stable_id(
            "release", intent["intent_id"]
        )
        approval = Phase8ApprovalBinding.model_validate(
            {
                "schema_version": "cad.phase8-approval-binding/1",
                "action": "program_commit",
                "intent_id": intent["intent_id"],
                "consent_id": consent["consent_id"],
                "intent_digest": intent["intent_digest"],
                "approval_proof_digest": (
                    "sha256:"
                    + hashlib.sha256(
                        canonical_json(
                            {
                                "consent_id": consent["consent_id"],
                                "intent_id": consent["intent_id"],
                                "intent_digest": consent["intent_digest"],
                                "state": consent["state"],
                                "state_version": consent["state_version"],
                                "decision_source": consent[
                                    "decision_source"
                                ],
                                "decision_principal": consent[
                                    "decision_principal"
                                ],
                                "decided_at": consent["decided_at"],
                            }
                        ).encode("utf-8")
                    ).hexdigest()
                ),
                "device_id": intent["device_id"],
                "document_id": intent["document_id"],
                "document_revision": intent[
                    "expected_document_revision"
                ],
                "job_id": job_id,
                "command_id": command_id,
                "idempotency_key": release_key,
                "source_digest": plan["source_digest"],
                "execution_plan_digest": plan["plan_digest"],
                "execution_binding_digest": binding[
                    "execution_binding_digest"
                ],
                "expansion_digest": plan["expansion_digest"],
                "effect_manifest_digest": plan["effect_digest"],
                "target_refs_digest": plan["target_set_digest"],
                "validation_profiles_digest": plan["plan"][
                    "validation_profiles_digest"
                ],
                "checkpoint_strategy_digest": plan["plan"][
                    "checkpoint_strategy_digest"
                ],
                "hard_budgets_digest": plan["plan"][
                    "hard_budgets_digest"
                ],
                "preview_id": intent["preview_id"],
                "preview_digest": intent["preview_digest"],
                "preview_expires_at": preview["expires_at"],
                "receipt_id": intent["deterministic_receipt_id"],
            }
        ).model_dump(mode="json")
        return {
            "binding": binding,
            "execution_plan": plan["plan"],
            "approval_binding": approval,
            "capability_evidence": admission["capability_evidence"],
            "preview_id": intent["preview_id"],
            "expires_at": preview["expires_at"],
            "preview_digest": intent["preview_digest"],
            "receipt_id": intent["deterministic_receipt_id"],
        }

    async def _phase8_preview_result(
        self, owner_subject: str, preview: dict[str, Any]
    ) -> ProgramPreviewResult:
        job = await self.repository.get_job(
            owner_subject, preview["job_id"]
        )
        if (
            job is None
            or job["state"] != "succeeded"
            or not isinstance(job.get("result"), dict)
        ):
            raise GatewayError("preview_unavailable")
        value = job["result"]
        plan = await self.phase8_repository.get_plan(
            owner_subject, preview["plan_id"]
        )
        if plan is None:
            raise GatewayError("preview_unavailable")
        entities = value.get("planned_entities")
        if not isinstance(entities, list):
            entities = [
                *(value.get("created_outputs") or []),
                *(value.get("modified_entities") or []),
            ]
        try:
            result = ProgramPreviewResult(
                preview_id=preview["preview_id"],
                preview_digest=value["preview_digest"],
                expires_at=preview["expires_at"],
                planned_operation_count=len(plan["plan"]["operations"]),
                planned_entity_count=len(entities),
                planned_layer_count=len(
                    {
                        item["layer"]
                        for item in entities
                        if isinstance(item, dict)
                        and isinstance(item.get("layer"), str)
                    }
                ),
                transaction_aborted=value["transaction_aborted"],
                drawing_unchanged=value["drawing_unchanged"],
            )
        except (KeyError, TypeError, ValueError):
            raise GatewayError("preview_unavailable") from None
        if (
            result.preview_id != preview["preview_id"]
            or result.expires_at != preview["expires_at"]
        ):
            raise GatewayError("binding_mismatch")
        return result

    def _phase8_approved_consent(
        self, intent: dict[str, Any]
    ) -> dict[str, Any]:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT consent_id FROM consents "
                "WHERE owner_subject = ? AND intent_id = ? AND state = 'approved'",
                (intent["owner_subject"], intent["intent_id"]),
            ).fetchone()
        if row is None:
            raise GatewayError("consent_not_approved")
        consent = self.phase7_repository
        if consent is None:
            raise GatewayError("feature_disabled")
        # This method is called from an async provider; reading through the
        # repository is performed by the caller before release.
        with self.database.read_connection() as conn:
            record = conn.execute(
                "SELECT * FROM consents WHERE consent_id = ?",
                (row["consent_id"],),
            ).fetchone()
        if record is None:
            raise GatewayError("consent_not_approved")
        value = dict(record)
        value["decision_principal"] = (
            json.loads(value.pop("decision_principal_json"))
            if value.get("decision_principal_json")
            else None
        )
        return value

    @staticmethod
    def _phase7_stable_id(prefix: str, *parts: str) -> str:
        material = "\0".join(parts).encode("utf-8")
        return f"{prefix}-{hashlib.sha256(material).hexdigest()[:40]}"

    async def prepare_program(
        self,
        request: Any,
        principal: Principal,
        correlation_id: str,
        *,
        schema_version: str = "cad.program/0.2",
        program_v1_source: dict[str, Any] | None = None,
        program_v1_revision_request: dict[str, Any] | None = None,
    ) -> Any:
        """Discriminate the existing public surface without changing v0.2."""

        if schema_version == "cad.program/0.2":
            if (
                program_v1_source is not None
                or program_v1_revision_request is not None
            ):
                raise GatewayError("invalid_request")
            return await self.program_service.prepare(
                request, principal, correlation_id
            )
        if schema_version != "cad.program/1.0":
            raise GatewayError("invalid_request")
        if not self.is_phase8 or self.phase8_gateway is None:
            raise GatewayError("feature_disabled")
        if "autocad.write" not in principal.scopes:
            raise GatewayError("insufficient_scope")
        if (program_v1_source is None) == (program_v1_revision_request is None):
            raise GatewayError("invalid_request")
        if program_v1_revision_request is not None:
            try:
                revision_request = CadPrepareProgramV1RevisionRequest.model_validate(
                    program_v1_revision_request
                )
                return await self._prepare_program_revision(
                    revision_request, principal, correlation_id
                )
            except (RepositoryConflict, ValueError) as error:
                code = getattr(error, "code", "invalid_request")
                if code in {
                    "revision_execution_started",
                    "revision_not_latest",
                }:
                    code = "binding_mismatch"
                raise GatewayError(self.program_service._repository_code(code)) from None
        if request is None or program_v1_source is None:
            raise GatewayError("invalid_request")
        if program_v1_source.get("schema_version") != "cad.program/1.0":
            raise GatewayError("invalid_request")
        if (
            program_v1_source.get("device_id") != request.device_id
            or program_v1_source.get("source_snapshot_id")
            != request.source_snapshot_id
            or program_v1_source.get("operations") != request.operations
        ):
            raise GatewayError("binding_mismatch")
        snapshot = await self.repository.get_snapshot(
            principal.subject, request.source_snapshot_id
        )
        if snapshot is None or snapshot["device_id"] != request.device_id:
            raise GatewayError("not_found")
        revision_evidence = snapshot.get("revision_evidence") or {}
        if (
            revision_evidence.get("commit_safe") is not True
            or revision_evidence.get("revision_strength") in {
                None,
                "summary_only",
            }
        ):
            raise GatewayError("stale_snapshot")
        document_id = self.program_service._snapshot_document_id(snapshot)
        if (
            program_v1_source.get("document_id") != document_id
            or program_v1_source.get("expected_document_revision")
            != snapshot["document_revision"]
            or program_v1_source.get("program_revision") != 1
        ):
            raise GatewayError("binding_mismatch")
        materialized_target_refs = self._phase8_materialized_target_refs(
            owner_subject=principal.subject,
            snapshot=snapshot,
            operations=request.operations,
            document_id=document_id,
        )
        try:
            prepared = await self.phase8_gateway.prepare_root(
                owner_subject=principal.subject,
                program_id=str(program_v1_source.get("program_id", "")),
                device_id=request.device_id,
                document_id=document_id,
                source_snapshot_id=request.source_snapshot_id,
                expected_document_revision=snapshot["document_revision"],
                source=program_v1_source,
                materialized_target_refs=materialized_target_refs,
            )
            sealed = prepared["plan"]
            binding = build_execution_binding_v1(
                sealed["plan"], action="compile_only"
            )
        except (RepositoryConflict, ValueError) as error:
            code = getattr(error, "code", "invalid_request")
            if code in {
                "compiler_unavailable",
                "source_binding_mismatch",
                "plan_id_mismatch",
            }:
                code = "capability_missing"
            else:
                code = self.program_service._repository_code(code)
            raise GatewayError(code) from None
        return CadPrepareProgramV1Output(
            correlation_id=correlation_id,
            program_id=sealed["program_id"],
            program_revision=sealed["program_revision"],
            source_digest=sealed["source_digest"],
            execution_plan_id=sealed["plan_id"],
            execution_plan_digest=sealed["plan_digest"],
            execution_binding=binding.model_dump(mode="json"),
            effect_manifest_digest=sealed["effect_digest"],
            document_id=document_id,
            expected_document_revision=snapshot["document_revision"],
            risk_class=sealed["risk_class"],
            resource_uri=(
                f"cad://programs/{sealed['program_id']}/revisions/"
                f"{sealed['program_revision']}"
            ),
            ready_for_preview=True,
        )

    async def _prepare_program_revision(
        self,
        request: CadPrepareProgramV1RevisionRequest,
        principal: Principal,
        correlation_id: str,
    ) -> Any:
        if self.phase8_gateway is None or self.phase8_repository is None:
            raise GatewayError("feature_disabled")
        parent = await self.phase8_repository.get_revision(
            principal.subject, request.program_id, request.source_revision
        )
        if parent is None:
            raise RepositoryConflict("not_found")
        old_snapshot = await self.repository.get_snapshot(
            principal.subject, parent["source_snapshot_id"]
        )
        if old_snapshot is None:
            raise RepositoryConflict("not_found")

        if request.kind == "patch":
            prepared = await self.phase8_gateway.patch(
                owner_subject=principal.subject,
                program_id=request.program_id,
                source_revision=request.source_revision,
                patch=request.changes or {},
                target_ref_resolver=lambda source: (
                    self._phase8_materialized_target_refs(
                        owner_subject=principal.subject,
                        snapshot=old_snapshot,
                        operations=source["operations"],
                        document_id=parent["document_id"],
                    )
                ),
            )
        else:
            new_snapshot = await self.repository.get_snapshot(
                principal.subject, request.new_snapshot_id or ""
            )
            if new_snapshot is None:
                raise RepositoryConflict("not_found")
            revision_evidence = new_snapshot.get("revision_evidence") or {}
            if (
                revision_evidence.get("commit_safe") is not True
                or revision_evidence.get("revision_strength")
                in {None, "summary_only"}
            ):
                raise RepositoryConflict("stale_snapshot")
            prepared = await self.phase8_gateway.rebase(
                owner_subject=principal.subject,
                program_id=request.program_id,
                source_revision=request.source_revision,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
                target_ref_resolver=lambda source: (
                    self._phase8_materialized_target_refs(
                        owner_subject=principal.subject,
                        snapshot=new_snapshot,
                        operations=source["operations"],
                        document_id=parent["document_id"],
                    )
                ),
            )

        revision = prepared["revision"]
        report = prepared["conflict_report"]
        if report is not None:
            return CadPrepareProgramV1ConflictOutput(
                correlation_id=correlation_id,
                program_id=request.program_id,
                program_revision=revision["revision"],
                lineage_kind=request.kind,
                conflict_report_id=report["conflict_report_id"],
                conflicts_digest=report["conflicts_digest"],
                resource_uri=(
                    f"cad://programs/{request.program_id}/revisions/"
                    f"{revision['revision']}"
                ),
            )
        sealed = prepared["plan"]
        binding = build_execution_binding_v1(
            sealed["plan"], action="compile_only"
        )
        return CadPrepareProgramV1Output(
            correlation_id=correlation_id,
            program_id=sealed["program_id"],
            program_revision=sealed["program_revision"],
            source_digest=sealed["source_digest"],
            execution_plan_id=sealed["plan_id"],
            execution_plan_digest=sealed["plan_digest"],
            execution_binding=binding.model_dump(mode="json"),
            effect_manifest_digest=sealed["effect_digest"],
            document_id=revision["document_id"],
            expected_document_revision=revision["expected_document_revision"],
            risk_class=sealed["risk_class"],
            resource_uri=(
                f"cad://programs/{sealed['program_id']}/revisions/"
                f"{sealed['program_revision']}"
            ),
            ready_for_preview=True,
        )

    async def read_program_resource(
        self, owner_subject: str, program_id: str, revision: int
    ) -> str:
        if self.is_phase8 and self.phase8_repository is not None:
            value = await self.phase8_repository.get_revision(
                owner_subject, program_id, revision
            )
            if value is not None:
                report = await self.phase8_repository.get_conflict_report_for_revision(
                    owner_subject, program_id, revision
                )
                if report is not None:
                    value["conflict_report"] = report
            return self.program_service._bounded_resource(value)
        return await self.program_service.read_program(
            owner_subject, program_id, revision
        )

    @staticmethod
    def _phase8_materialized_target_refs(
        *,
        owner_subject: str,
        snapshot: dict[str, Any],
        operations: list[dict[str, Any]],
        document_id: str,
    ) -> list[dict[str, Any]] | None:
        target_kinds = {"copy_entity", "offset_entity", "move_entity"}
        requested_values = [
            operation.get("target_ref_id")
            for operation in operations
            if operation.get("kind") in target_kinds
        ]
        if not requested_values:
            return None
        if any(
            not isinstance(ref_id, str) or not ref_id
            for ref_id in requested_values
        ):
            raise GatewayError("invalid_request")
        requested = list(dict.fromkeys(requested_values))
        indexed: dict[str, dict[str, Any]] = {}
        for entity in snapshot.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            for key in ("ref_id", "entity_id", "handle"):
                value = entity.get(key)
                if isinstance(value, str) and value:
                    if value in indexed and indexed[value] != entity:
                        raise GatewayError("binding_mismatch")
                    indexed[value] = entity
        result: list[dict[str, Any]] = []
        for ref_id in requested:
            entity = indexed.get(ref_id)
            if entity is None:
                raise GatewayError("not_found")
            entity_id = entity.get("entity_id") or entity.get("handle")
            entity_type = entity.get("entity_type") or entity.get("type")
            fingerprint = entity.get("fingerprint")
            if not all(
                isinstance(value, str) and value
                for value in (entity_id, entity_type, fingerprint)
            ):
                raise GatewayError("stale_snapshot")
            result.append(
                {
                    "ref_id": ref_id,
                    "owner_id": owner_subject,
                    "device_id": snapshot["device_id"],
                    "document_id": document_id,
                    "snapshot_id": snapshot["snapshot_id"],
                    "document_revision": snapshot["document_revision"],
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "fingerprint": fingerprint,
                }
            )
        return result

    async def preview_program(
        self, request: Any, principal: Principal, correlation_id: str
    ) -> Any:
        if self.phase8_repository is None or self.phase8_gateway is None:
            return await self.program_service.preview(
                request, principal, correlation_id
            )
        plan = await self.phase8_repository.get_plan_for_program(
            principal.subject, request.program_id, request.program_revision
        )
        if plan is None:
            return await self.program_service.preview(
                request, principal, correlation_id
            )
        if not self.is_phase8:
            raise GatewayError("feature_disabled")
        self.program_service._require_write_scope(principal)
        self.program_service._require_managed_write()
        self.program_service._require_allowed_device(plan["plan"]["device_id"])
        connection, current_pins = await self._phase8_current_binding(plan)
        admission = await self._phase8_admit(
            principal=principal,
            plan=plan,
            connection=connection,
            current_pins=current_pins,
            action="preview",
        )
        key = request.idempotency_key or f"preview-{uuid.uuid4()}"
        preview_id = (
            "preview-v1-"
            + hashlib.sha256(
                (
                    principal.subject
                    + "\0"
                    + plan["plan_id"]
                    + "\0"
                    + key
                ).encode("utf-8")
            ).hexdigest()[:48]
        )
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=10)).isoformat()
        deadline_at = (now + timedelta(seconds=120)).isoformat()
        binding = build_execution_binding_v1(
            plan["plan"],
            action="preview",
            preview_id=preview_id,
            preview_expires_at=expires_at,
        ).model_dump(mode="json")
        payload = {
            "binding": binding,
            "execution_plan": plan["plan"],
            "capability_evidence": admission["capability_evidence"],
            "preview_id": preview_id,
            "expires_at": expires_at,
        }
        request_digest = (
            "sha256:"
            + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        )
        try:
            job = await self.repository.create_job(
                owner_subject=principal.subject,
                device_id=plan["plan"]["device_id"],
                kind="program_preview",
                effect_class="write",
                payload=payload,
                idempotency_key=key,
                deadline_at=deadline_at,
            )
            preview, duplicate = await self.phase8_repository.create_preview(
                owner_subject=principal.subject,
                plan_id=plan["plan_id"],
                preview_id=preview_id,
                job_id=job["job_id"],
                execution_binding=binding,
                capability_evidence_ids=admission[
                    "capability_evidence_ids"
                ],
                expires_at=expires_at,
                idempotency_key=key,
                request_digest=request_digest,
            )
            await self.phase8_repository.append_usage_event(
                owner_subject=principal.subject,
                plan_id=plan["plan_id"],
                state="previewed",
                external_id=preview_id,
                binding_digest=admission["binding_digest"],
            )
        except RepositoryConflict as error:
            raise GatewayError(
                self.program_service._repository_code(error.code)
            ) from None
        del duplicate
        return CadPreviewOutput(
            correlation_id=correlation_id,
            program_id=plan["program_id"],
            program_revision=plan["program_revision"],
            preview_id=preview["preview_id"],
            job_id=job["job_id"],
            state=job["state"],
            program_digest=plan["source_digest"],
            execution_digest=binding["execution_binding_digest"],
            binding_digest=admission["binding_digest"],
            planned_operation_count=len(plan["plan"]["operations"]),
            planned_entity_count=plan["create_count"],
            planned_layer_count=plan["effect_manifest"][
                "ensures_non_entity"
            ],
            validation=None,
            expires_at=expires_at,
            job_uri=f"cad://jobs/{job['job_id']}",
            resource_uri=f"cad://previews/{preview_id}",
        )

    async def _phase8_current_binding(
        self, plan: dict[str, Any]
    ) -> tuple[Any, dict[str, str]]:
        device_id = plan["plan"]["device_id"]
        connection = await self.registry.get(device_id)
        if (
            connection is None
            or not await self.registry.is_current_and_fresh(connection)
        ):
            raise GatewayError("device_offline")
        if (
            connection.active_document_id != plan["plan"]["document_id"]
            or connection.active_document_revision
            != plan["plan"]["expected_document_revision"]
        ):
            raise GatewayError("stale_revision")
        expected = dict(plan["runtime_pins"])
        try:
            capability_hash = normalize_sha256_digest(
                connection.capability_manifest_hash
            )
            registry_hash = normalize_sha256_digest(
                connection.operation_registry_hash
            )
        except (TypeError, ValueError):
            raise GatewayError("capability_missing") from None
        if (
            capability_hash != expected["capability_manifest_hash"]
            or registry_hash != expected["operation_registry_hash"]
            or connection.registry_version
            != expected["operation_registry_version"]
        ):
            raise GatewayError("binding_mismatch")
        manifest = connection.capability_manifest
        candidates = [
            item["runtime"]
            for item in (manifest or {}).get("cad_products", [])
            if isinstance(item, dict)
            and isinstance(item.get("runtime"), dict)
            and item.get("edition") == "full"
            and item.get("release_year") == 2025
        ]
        if len(candidates) != 1:
            raise GatewayError("capability_missing")
        runtime = candidates[0]
        actual = {
            **expected,
            "runtime_id": runtime.get("id"),
            "host_family": runtime.get("host_family"),
            "host_version": runtime.get("host_version"),
            "package_id": runtime.get("package_id"),
            "package_version": runtime.get("package_version"),
            "package_hash": runtime.get("package_hash"),
            "capability_manifest_hash": capability_hash,
            "operation_registry_hash": registry_hash,
        }
        if actual != expected:
            raise GatewayError("capability_missing")
        return connection, actual

    async def _phase8_admit(
        self,
        *,
        principal: Principal,
        plan: dict[str, Any],
        connection: Any,
        current_pins: dict[str, str],
        action: str,
    ) -> dict[str, Any]:
        manifest = connection.capability_manifest
        reported_capabilities = tuple(
            capability
            for product in (manifest or {}).get("cad_products", [])
            if isinstance(product, dict)
            and isinstance(product.get("runtime"), dict)
            and product["runtime"].get("id") == current_pins["runtime_id"]
            and product["runtime"].get("host_family")
            == current_pins["host_family"]
            and product["runtime"].get("package_hash")
            == current_pins["package_hash"]
            for capability in product.get("capabilities", [])
            if isinstance(capability, str)
        )
        try:
            return await self.phase8_gateway.admit(
                owner_subject=principal.subject,
                device_id=plan["plan"]["device_id"],
                plan_id=plan["plan_id"],
                action=action,
                cohort="lab",
                reported_capabilities=reported_capabilities,
                current_runtime_pins=current_pins,
            )
        except RepositoryConflict as error:
            raise GatewayError(
                self.program_service._repository_code(error.code)
            ) from None

    async def decide_phase7_local_approval(
        self, decision: dict[str, Any]
    ) -> dict[str, Any]:
        if self.phase7_admission is None:
            raise GatewayError("feature_disabled")
        return await self.phase7_admission.local_decide(decision)

    async def on_agent_message(self, connection: Any, message: Any) -> None:
        if getattr(message, "message_type", None) == "approval_decision":
            await self.decide_phase7_local_approval(message)
            return
        await self.job_service.handle_message(connection, message)

    async def validate_agent_message(self, connection: Any, message: Any) -> bool:
        if getattr(message, "message_type", None) == "approval_decision":
            return bool(
                self.phase7_admission is not None
                and getattr(message, "device_id", None) == connection.device_id
                and getattr(message, "session_id", None) == connection.session_id
            )
        return await self.job_service.validate_message(connection, message)

    async def shutdown(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
            await asyncio.gather(self._maintenance_task, return_exceptions=True)
            self._maintenance_task = None
        self.job_service.cancel_waiters()
        await self.registry.close_all()
        await self.database.close()
        self._initialized = False

    async def _maintenance_loop(self) -> None:
        interval = self.maintenance_interval_seconds
        if interval is None:
            interval = max(1.0, min(self.registry.stale_after_seconds / 2, 30.0))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._run_maintenance_once()
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise
                logger.warning("Transient SQLite maintenance contention", exc_info=True)

    async def _run_maintenance_once(self) -> None:
        for connection in await self.registry.stale_connections():
            if not await self.registry.is_current(connection):
                continue
            if await self.registry.is_current_and_fresh(connection):
                continue
            marked = await self.repository.mark_session_stale(
                connection.session_id,
                device_id=connection.device_id,
            )
            if (
                marked
                and await self.registry.is_current(connection)
                and not await self.registry.is_current_and_fresh(connection)
            ):
                await self.job_service.handle_disconnect(connection.device_id)
        await self.job_service.sweep_deadlines()
        if self.workflow_service is not None:
            await self.workflow_service.maintenance_once()

    def _maintenance_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            error = RuntimeError("durable maintenance stopped unexpectedly")
        self._maintenance_error = error
        logger.error(
            "Durable maintenance task stopped",
            exc_info=(type(error), error, error.__traceback__),
        )

    def is_ready(self) -> bool:
        task = self._maintenance_task
        try:
            migrations_valid = self.database.verify_migration_state()
        except DatabaseError:
            migrations_valid = False
        return bool(
            self._initialized
            and self.database.is_open
            and migrations_valid
            and self._maintenance_error is None
            and task is not None
            and not task.done()
        )

    async def on_agent_connected(self, connection: Any) -> None:
        if self.is_phase4 and not self._package_matches(connection):
            await self.repository.set_device_status(connection.device_id, "incompatible")
            raise DurableJobError("package_mismatch")
        session = await self.repository.activate_session(
            device_id=connection.device_id,
            session_id=connection.session_id,
            protocol_version=connection.protocol_version,
            capabilities=list(connection.capabilities),
            capability_hash=connection.capability_hash,
            last_sequence=connection.last_sequence,
            agent_version=connection.agent_version,
            packages=list(connection.packages),
            package_manifest_hash=connection.package_manifest_hash,
            runtime_state=connection.runtime_state,
            document_name=connection.document_name,
            paused=connection.paused,
            capability_manifest=connection.capability_manifest,
            capability_manifest_hash=connection.capability_manifest_hash,
            operation_registry_hash=connection.operation_registry_hash,
            registry_version=connection.registry_version,
            write_lock_enabled=connection.write_lock_enabled,
            hard_pause=connection.hard_pause,
            active_document_id=connection.active_document_id,
            active_document_revision=connection.active_document_revision,
        )
        if session["capability_changed"]:
            logger.info(
                "Agent capability manifest changed",
                extra={
                    "device_id": connection.device_id,
                    "capability_hash": connection.capability_hash,
                },
            )
        await self.job_service.handle_connected(connection)
        if self.workflow_service is not None:
            await self.workflow_service.maintenance_once()

    async def on_agent_heartbeat(self, connection: Any, message: Any) -> None:
        phase6_state_present = bool(
            {
                "write_lock_enabled",
                "hard_pause",
                "active_document_id",
                "active_document_revision",
            }
            & message.model_fields_set
        )
        updated = await self.repository.heartbeat_session(
            connection.session_id,
            device_id=connection.device_id,
            sequence=message.sequence,
            runtime_state=message.runtime_state,
            document_name=message.document_name,
            paused=message.paused,
            write_lock_enabled=message.write_lock_enabled,
            hard_pause=message.hard_pause,
            active_document_id=message.active_document_id,
            active_document_revision=message.active_document_revision,
            phase6_state_present=phase6_state_present,
        )
        if not updated:
            raise DurableJobError("invalid_message")

    async def on_agent_disconnected(self, connection: Any) -> None:
        await self.repository.close_session(connection.session_id, device_id=connection.device_id)
        is_current = getattr(self.registry, "is_current", None)
        current_connection = (
            await is_current(connection)
            if is_current is not None
            else (await self.registry.get(connection.device_id)) is connection
        )
        if current_connection:
            await self.job_service.handle_disconnect(connection.device_id)

    async def list_devices(
        self, request: CadListDevicesInput, principal: Principal, correlation_id: str
    ) -> CadListDevicesOutput | CadListDevicesOutputC1:
        if not self.is_phase5_identity and principal.subject != self.owner_subject:
            output_type = CadListDevicesOutputC1 if self.is_phase4 else CadListDevicesOutput
            return output_type(
                contract_version=self.contract_version,
                correlation_id=correlation_id,
                devices=[],
            )
        devices = await self.repository.list_devices(
            principal.subject, online_only=request.online_only, capability=request.capability
        )
        output_type = CadListDevicesOutputC1 if self.is_phase4 else CadListDevicesOutput
        device_type = DeviceInfoC1 if self.is_phase4 else DeviceInfo
        return output_type(
            contract_version=self.contract_version,
            correlation_id=correlation_id,
            devices=[
                device_type(
                    device_id=value["device_id"],
                    display_name=value["display_name"],
                    status=(
                        value["status"]
                        if self.is_phase4 and value["status"] == "incompatible"
                        else "online" if value["status"] == "online" else "offline"
                    ),
                    capabilities=value["capabilities"],
                    **(
                        {
                            "runtime_state": value.get("runtime_state"),
                            "document_name": value.get("document_name"),
                            "last_seen_at": value.get("runtime_updated_at"),
                            "agent_version": value.get("agent_version"),
                            "package_summary": value.get("packages", []),
                            "paused": value.get("paused", False),
                        }
                        if self.is_phase4
                        else {}
                    ),
                )
                for value in devices
            ],
            default_device_id=devices[0]["device_id"] if devices else None,
        )

    async def observe(
        self,
        request: CadObserveInput | CadObserveInputDurable,
        principal: Principal,
        correlation_id: str,
    ) -> CadObserveOutputDurable | CadObserveOutputC1:
        device = await self._require_device(request.device_id, principal)
        if "observe" not in device["capabilities"]:
            raise GatewayError("capability_missing")
        if request.include_preview_image:
            raise GatewayError("capability_missing")
        payload = {
            "observation_level": request.observation_level,
            "include_preview_image": request.include_preview_image,
        }
        if self.is_phase5_identity:
            observation_packages = [
                package
                for package in device.get("packages", [])
                if package.get("package_id") == "autocad.lisp.drawing_info"
            ]
            if len(observation_packages) != 1:
                raise GatewayError("package_mismatch")
            payload["package"] = observation_packages[0]
        elif self.is_phase4:
            payload["package"] = self.required_package
        explicit_key = getattr(request, "idempotency_key", None)
        key = explicit_key or f"observe-{uuid.uuid4()}"
        deadline_at = (
            datetime.now(timezone.utc) + timedelta(seconds=self.job_deadline_seconds)
        ).isoformat()
        try:
            job = await self.job_service.create_and_observe(
                owner_subject=principal.subject,
                device_id=request.device_id,
                payload=payload,
                correlation_id=correlation_id,
                idempotency_key=key,
                deadline_at=deadline_at,
            )
        except DurableJobError as error:
            raise GatewayError(
                self._safe_job_error_code(error.code),
                job_id=error.job_id,
                job_state=error.job_state,
            ) from None
        if job["state"] != "succeeded":
            code = (
                "job_in_progress"
                if job["state"]
                in {
                    "queued",
                    "dispatched",
                    "acknowledged",
                    "running",
                    "cancel_requested",
                    "reconnect_pending",
                    "outcome_unknown",
                }
                else self._safe_job_error_code(job.get("error_code"))
            )
            raise GatewayError(
                code,
                job_id=job["job_id"],
                job_state=job["state"],
            )
        if not job.get("result"):
            raise GatewayError(
                "backend_error",
                job_id=job["job_id"],
                job_state=job["state"],
            )
        snapshot = job["result"].get("snapshot")
        if not isinstance(snapshot, dict):
            raise GatewayError("backend_error")
        entity_count = int(
            snapshot.get("entity_summary", {}).get(
                "entity_count", len(snapshot.get("entities", []))
            )
        )
        if self.is_phase4:
            evidence = job["result"].get("execution_evidence", {})
            package = evidence.get("package") or self.required_package
            return CadObserveOutputC1(
                correlation_id=correlation_id,
                device_id=request.device_id,
                snapshot_id=str(snapshot["snapshot_id"]),
                document_revision=str(snapshot["document_revision"]),
                observation_level=request.observation_level,
                entity_count=entity_count,
                summary_uri=f"cad://snapshots/{snapshot['snapshot_id']}/summary",
                entities_uri=f"cad://snapshots/{snapshot['snapshot_id']}/entities",
                artifact_refs=[],
                job_id=job["job_id"],
                revision_evidence=RevisionEvidence.model_validate(
                    snapshot.get("revision_evidence", {})
                ),
                execution_evidence=ExecutionEvidence(
                    agent_version=str(evidence.get("agent_version", "unknown")),
                    command_id=job["command_id"],
                    package=PackageEvidence.model_validate(package),
                    runtime_state=evidence.get("runtime_state"),
                ),
            )
        return CadObserveOutputDurable(
            correlation_id=correlation_id,
            device_id=request.device_id,
            snapshot_id=str(snapshot["snapshot_id"]),
            document_revision=str(snapshot["document_revision"]),
            observation_level=request.observation_level,
            entity_count=entity_count,
            summary_uri=f"cad://snapshots/{snapshot['snapshot_id']}/summary",
            entities_uri=f"cad://snapshots/{snapshot['snapshot_id']}/entities",
            artifact_refs=[],
            job_id=job["job_id"],
        )

    async def query(
        self, request: CadQueryInput, principal: Principal, correlation_id: str
    ) -> CadQueryOutput:
        snapshot = await self.repository.get_snapshot(principal.subject, request.snapshot_id)
        if snapshot is None:
            raise GatewayError("not_found")
        if self.is_phase4 and snapshot.get("revision_evidence", {}).get("revision_strength") == "summary_only":
            raise GatewayError("capability_missing")
        selected = [
            entity
            for entity in snapshot["entities"]
            if (
                not request.types
                or str(entity["entity_type"]).upper() in request.types
            )
            and (not request.layers or entity["layer"] in request.layers)
        ]
        offset = 0
        if request.cursor:
            try:
                cursor = decode_cursor(request.cursor)
            except ValueError:
                raise GatewayError("invalid_request") from None
            if (
                cursor.get("snapshot_id") != request.snapshot_id
                or cursor.get("filter_hash")
                != cursor_filter_hash(request.types, request.layers)
            ):
                raise GatewayError("invalid_request")
            offset = cursor["offset"]
        if offset > len(selected):
            raise GatewayError("invalid_request")
        page = selected[offset : offset + request.limit]
        next_cursor = None
        if offset + request.limit < len(selected):
            next_cursor = encode_cursor(
                snapshot_id=request.snapshot_id,
                types=request.types,
                layers=request.layers,
                offset=offset + request.limit,
            )
        return CadQueryOutput(
            contract_version=PHASE3_CONTRACT_VERSION,
            correlation_id=correlation_id,
            snapshot_id=request.snapshot_id,
            document_revision=snapshot["document_revision"],
            entities=[
                CadEntity.model_validate(copy.deepcopy(entity), extra="ignore")
                for entity in page
            ],
            total=len(selected),
            next_cursor=next_cursor,
            resource_uri=f"cad://snapshots/{request.snapshot_id}/entities",
        )

    async def get_job(
        self, request: CadGetJobInput, principal: Principal, correlation_id: str
    ) -> CadGetJobOutput | CadGetJobOutputC1:
        job = await self.repository.get_job(principal.subject, request.job_id)
        if job is None:
            raise GatewayError("not_found")
        cursor = 0
        if request.event_cursor:
            try:
                cursor = int(request.event_cursor)
            except ValueError:
                raise GatewayError("invalid_request") from None
            if cursor < 0:
                raise GatewayError("invalid_request")
        events, next_cursor = await self.repository.list_events(
            principal.subject, request.job_id, cursor=cursor, limit=request.event_limit
        )
        result = job.get("result")
        snapshot_id = None
        if isinstance(result, dict) and isinstance(result.get("snapshot"), dict):
            snapshot_id = result["snapshot"].get("snapshot_id")
        common = dict(
            correlation_id=correlation_id,
            job_id=job["job_id"],
            device_id=job["device_id"],
            kind=job["kind"],
            state=job["state"],
            progress=job.get("progress"),
            result=result,
            error_code=job.get("error_code"),
            error_summary=job.get("error_summary"),
            events=[CadJobEvent.model_validate(event) for event in events],
            next_event_cursor=next_cursor,
            snapshot_id=snapshot_id,
        )
        if self.is_phase4:
            evidence = result.get("execution_evidence", {}) if isinstance(result, dict) else {}
            package = evidence.get("package")
            return CadGetJobOutputC1(
                **common,
                agent_version=evidence.get("agent_version"),
                command_id=job["command_id"],
                package=PackageEvidence.model_validate(package) if package else None,
                runtime_evidence=evidence or None,
            )
        return CadGetJobOutput(**common)

    async def read_device_capabilities(self, device_id: str, principal: Principal) -> str:
        value = await self._require_device(device_id, principal)
        return json.dumps(
            {
                "contract_version": self.contract_version,
                "device_id": device_id,
                "status": value["status"],
                "capabilities": value["capabilities"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    async def read_snapshot_summary(self, snapshot_id: str, principal: Principal) -> str:
        snapshot = await self.repository.get_snapshot(principal.subject, snapshot_id)
        if snapshot is None:
            raise GatewayError("not_found")
        return canonical_json(
            {
                "contract_version": self.contract_version,
                "snapshot_id": snapshot["snapshot_id"],
                "device_id": snapshot["device_id"],
                "job_id": snapshot["job_id"],
                "document_revision": snapshot["document_revision"],
                "observation_level": snapshot["observation_level"],
                "drawing": snapshot["drawing"],
                "entity_summary": snapshot["entity_summary"],
                "entity_count": snapshot.get("entity_summary", {}).get(
                    "entity_count", len(snapshot["entities"])
                ),
                "revision_evidence": snapshot.get("revision_evidence"),
            }
        )

    async def read_snapshot_entities(
        self,
        snapshot_id: str,
        principal: Principal,
        *,
        types: list[str] | None = None,
        layers: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        correlation_id: str | None = None,
    ) -> str:
        result = await self.query(
            CadQueryInput(
                snapshot_id=snapshot_id,
                types=types or [],
                layers=layers or [],
                cursor=cursor,
                limit=limit,
            ),
            principal,
            correlation_id or str(uuid.uuid4()),
        )
        return result.model_dump_json()

    async def read_job_resource(self, job_id: str, principal: Principal) -> str:
        result = await self.get_job(
            CadGetJobInput(job_id=job_id), principal, str(uuid.uuid4())
        )
        return result.model_dump_json()

    async def read_artifact(self, artifact_id: str, principal: Principal) -> bytes:
        del artifact_id, principal
        # Durable preview artifacts are not implemented in Phase 3. The advertised
        # additive resource remains fail-closed until a bounded owner-scoped store exists.
        raise GatewayError("not_found")

    async def _require_device(self, device_id: str, principal: Principal) -> dict[str, Any]:
        if not self.is_phase5_identity and principal.subject != self.owner_subject:
            raise GatewayError("not_found")
        value = await self.repository.get_device(principal.subject, device_id)
        if value is None:
            raise GatewayError("not_found")
        return value

    @property
    def contract_version(self) -> str:
        return PHASE4_CONTRACT_VERSION if self.is_phase4 else PHASE3_CONTRACT_VERSION

    def _package_matches(self, connection: Any) -> bool:
        if not self.required_package:
            return True
        return self.required_package in list(connection.packages)

    @staticmethod
    def _safe_job_error_code(error_code: str | None) -> str:
        if error_code in _SAFE_JOB_ERROR_CODES:
            return error_code
        return "backend_error"
