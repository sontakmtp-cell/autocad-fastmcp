"""Phase 3 durable application facade used by the thin FastMCP layer."""

from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import uuid
from typing import Any

from .application.job_service import DurableJobError, DurableJobService
from .contracts import (
    CadEntity,
    CadGetJobInput,
    CadGetJobOutput,
    CadJobEvent,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveOutputDurable,
    CadQueryInput,
    CadQueryOutput,
    DeviceInfo,
    PHASE3_CONTRACT_VERSION,
    Principal,
)
from .services import GatewayError
from .snapshots import canonical_json, decode_cursor, encode_cursor
from .infrastructure.agent_transport.connection_registry import ConnectionRegistry
from .infrastructure.agent_transport.authenticator import FixtureDeviceAuthenticator
from .infrastructure.sqlite.database import SqliteDatabase
from .infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository


PHASE3_OWNER = "phase3-fixture-user"
PHASE3_CAPABILITIES = ["observe", "query"]


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
        command_timeout_seconds: int = 30,
    ) -> None:
        self.database = database
        self.registry = registry
        self.repository = SqliteRepository(database)
        self.job_service = DurableJobService(
            self.repository, registry, command_timeout_seconds=command_timeout_seconds
        )
        self.device_tokens = dict(device_tokens)
        self.agent_authenticator = FixtureDeviceAuthenticator(self.device_tokens)
        self.owner_subject = owner_subject
        self._initialized = False
        self._maintenance_task: asyncio.Task[None] | None = None

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
                except Exception:
                    pass
        self._initialized = True
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def shutdown(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
            await asyncio.gather(self._maintenance_task, return_exceptions=True)
            self._maintenance_task = None
        await self.registry.close_all()
        await self.database.close()
        self._initialized = False

    async def _maintenance_loop(self) -> None:
        interval = max(1, min(self.registry.stale_after_seconds // 2, 30))
        while True:
            await asyncio.sleep(interval)
            for device_id in await self.registry.stale_devices():
                await self.repository.set_device_status(device_id, "offline")
                await self.job_service.handle_disconnect(device_id)
            await self.job_service.sweep_deadlines()

    async def on_agent_connected(self, connection: Any) -> None:
        await self.repository.create_session(
            device_id=connection.device_id,
            session_id=connection.session_id,
            protocol_version=connection.protocol_version,
        )
        await self.job_service.handle_connected(connection)

    async def on_agent_disconnected(self, connection: Any) -> None:
        await self.repository.close_session(connection.session_id, device_id=connection.device_id)
        current = await self.registry.get(connection.device_id)
        if current is None or current.session_id == connection.session_id:
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
        self, request: CadObserveInput, principal: Principal, correlation_id: str
    ) -> CadObserveOutputDurable:
        await self._require_device(request.device_id, principal)
        if request.include_preview_image:
            raise GatewayError("capability_missing")
        payload = {
            "observation_level": request.observation_level,
            "include_preview_image": request.include_preview_image,
        }
        key = "observe-" + hashlib.sha256(
            canonical_json({"device_id": request.device_id, **payload}).encode("utf-8")
        ).hexdigest()
        try:
            job = await self.job_service.create_and_observe(
                owner_subject=principal.subject,
                device_id=request.device_id,
                payload=payload,
                correlation_id=correlation_id,
                idempotency_key=key,
                deadline_at=None,
            )
        except DurableJobError as error:
            raise GatewayError(error.code) from None
        if job["state"] != "succeeded" or not job.get("result"):
            raise GatewayError(job.get("error_code") or "job_in_progress")
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
            if (not request.types or entity["entity_type"] in request.types)
            and (not request.layers or entity["layer"] in request.layers)
        ]
        offset = 0
        if request.cursor:
            try:
                cursor = decode_cursor(request.cursor)
            except Exception:
                raise GatewayError("invalid_request") from None
            if cursor.get("snapshot_id") != request.snapshot_id or cursor.get("types") != request.types or cursor.get("layers") != request.layers:
                raise GatewayError("invalid_request")
            offset = cursor["offset"]
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

    async def _require_device(self, device_id: str, principal: Principal) -> dict[str, Any]:
        if principal.subject != self.owner_subject:
            raise GatewayError("not_found")
        value = await self.repository.get_device(principal.subject, device_id)
        if value is None:
            raise GatewayError("not_found")
        return value
