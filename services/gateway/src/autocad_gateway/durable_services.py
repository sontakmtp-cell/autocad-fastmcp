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
from typing import Any

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


PHASE3_OWNER = "phase3-fixture-user"
PHASE3_CAPABILITIES = ["observe", "query"]
logger = logging.getLogger(__name__)

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
    ) -> None:
        self.database = database
        self.registry = registry
        self.repository = SqliteRepository(database)
        self.is_phase7 = profile == "phase7_c2"
        self.is_phase6 = profile in {"phase6_program", "phase7_c2"}
        self.program_repository = ProgramRepository(database) if self.is_phase6 else None
        self.phase7_repository = Phase7Repository(database) if self.is_phase7 else None
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
        }:
            self.agent_authenticator = FixtureDeviceAuthenticator(self.device_tokens)
        self.owner_subject = owner_subject
        self.profile = profile
        self.is_phase4 = profile in {
            "phase4_c1",
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
        }
        self.is_phase5_identity = profile in {
            "phase5_identity",
            "phase6_program",
            "phase7_c2",
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
        self.phase6_direct_commit_lab_enabled = phase6_direct_commit_lab_enabled

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

    async def commit_program(
        self, request: Any, principal: Principal, correlation_id: str
    ) -> Any:
        if self.phase7_admission is not None:
            return await self.phase7_admission.commit(
                request, principal, correlation_id
            )
        if not self.phase6_direct_commit_lab_enabled:
            raise GatewayError("feature_disabled")
        return await self.program_service.commit(request, principal, correlation_id)

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
            entities=[CadEntity.model_validate(copy.deepcopy(entity)) for entity in page],
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
