"""Phase 3 durable application facade used by the thin FastMCP layer."""

from __future__ import annotations

import copy
import asyncio
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
    CadJobEvent,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveInputDurable,
    CadObserveOutputDurable,
    CadQueryInput,
    CadQueryOutput,
    DeviceInfo,
    PHASE3_CONTRACT_VERSION,
    Principal,
)
from .services import GatewayError
from .snapshots import canonical_json, cursor_filter_hash, decode_cursor, encode_cursor
from .infrastructure.agent_transport.connection_registry import ConnectionRegistry
from .infrastructure.agent_transport.authenticator import FixtureDeviceAuthenticator
from .infrastructure.sqlite.database import DatabaseError, SqliteDatabase
from .infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository


PHASE3_OWNER = "phase3-fixture-user"
PHASE3_CAPABILITIES = ["observe", "query"]
logger = logging.getLogger(__name__)

_SAFE_JOB_ERROR_CODES = frozenset(
    {
        "agent_rejected",
        "backend_error",
        "capability_missing",
        "deadline_expired",
        "device_offline",
        "idempotency_conflict",
        "payload_mismatch",
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
    ) -> None:
        self.database = database
        self.registry = registry
        self.repository = SqliteRepository(database)
        self.job_service = DurableJobService(
            self.repository,
            registry,
            request_wait_timeout_seconds=request_wait_timeout_seconds,
        )
        self.device_tokens = dict(device_tokens)
        self.agent_authenticator = FixtureDeviceAuthenticator(self.device_tokens)
        self.owner_subject = owner_subject
        self.job_deadline_seconds = max(1.0, min(float(job_deadline_seconds), 86_400.0))
        self.maintenance_interval_seconds = maintenance_interval_seconds
        self._initialized = False
        self._maintenance_task: asyncio.Task[None] | None = None
        self._maintenance_error: BaseException | None = None

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.database.open()
        await self.repository.mark_sessions_disconnected()
        for device_id in self.device_tokens:
            await self.repository.seed_device(
                owner_subject=self.owner_subject,
                device_id=device_id,
                display_name=f"Simulated {device_id}",
                capabilities=PHASE3_CAPABILITIES,
                fixture_auth_ref=f"fixture:{device_id}",
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
        session = await self.repository.activate_session(
            device_id=connection.device_id,
            session_id=connection.session_id,
            protocol_version=connection.protocol_version,
            capabilities=list(connection.capabilities),
            capability_hash=connection.capability_hash,
            last_sequence=connection.last_sequence,
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
        updated = await self.repository.heartbeat_session(
            connection.session_id,
            device_id=connection.device_id,
            sequence=message.sequence,
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
    ) -> CadListDevicesOutput:
        if principal.subject != self.owner_subject:
            return CadListDevicesOutput(
                contract_version=PHASE3_CONTRACT_VERSION,
                correlation_id=correlation_id,
                devices=[],
            )
        devices = await self.repository.list_devices(
            principal.subject, online_only=request.online_only, capability=request.capability
        )
        return CadListDevicesOutput(
            contract_version=PHASE3_CONTRACT_VERSION,
            correlation_id=correlation_id,
            devices=[
                DeviceInfo(
                    device_id=value["device_id"],
                    display_name=value["display_name"],
                    status="online" if value["status"] == "online" else "offline",
                    capabilities=value["capabilities"],
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
    ) -> CadObserveOutputDurable:
        device = await self._require_device(request.device_id, principal)
        if "observe" not in device["capabilities"]:
            raise GatewayError("capability_missing")
        if request.include_preview_image:
            raise GatewayError("capability_missing")
        payload = {
            "observation_level": request.observation_level,
            "include_preview_image": request.include_preview_image,
        }
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
        return CadObserveOutputDurable(
            correlation_id=correlation_id,
            device_id=request.device_id,
            snapshot_id=str(snapshot["snapshot_id"]),
            document_revision=str(snapshot["document_revision"]),
            observation_level=request.observation_level,
            entity_count=len(snapshot.get("entities", [])),
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
    ) -> CadGetJobOutput:
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
        return CadGetJobOutput(
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

    async def read_device_capabilities(self, device_id: str, principal: Principal) -> str:
        value = await self._require_device(device_id, principal)
        return json.dumps(
            {
                "contract_version": PHASE3_CONTRACT_VERSION,
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
                "contract_version": PHASE3_CONTRACT_VERSION,
                "snapshot_id": snapshot["snapshot_id"],
                "device_id": snapshot["device_id"],
                "job_id": snapshot["job_id"],
                "document_revision": snapshot["document_revision"],
                "observation_level": snapshot["observation_level"],
                "drawing": snapshot["drawing"],
                "entity_summary": snapshot["entity_summary"],
                "entity_count": len(snapshot["entities"]),
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
        if principal.subject != self.owner_subject:
            raise GatewayError("not_found")
        value = await self.repository.get_device(principal.subject, device_id)
        if value is None:
            raise GatewayError("not_found")
        return value

    @staticmethod
    def _safe_job_error_code(error_code: str | None) -> str:
        if error_code in _SAFE_JOB_ERROR_CODES:
            return error_code
        return "backend_error"
