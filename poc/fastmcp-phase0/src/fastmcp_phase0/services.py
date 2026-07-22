"""Small in-memory application service layer used by the spike."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from cad_core import CadApplicationService, CadInvocation, CommandResult
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

from .contracts import (
    CONTRACT_VERSION,
    ArtifactRef,
    CadGetJobInput,
    CadGetJobOutput,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveOutput,
    DeviceInfo,
)


MAX_PREVIEW_BYTES = 2_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Principal:
    """Identity passed from the MCP boundary into the domain service."""

    subject: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactPayload:
    """Transport-neutral artifact bytes plus the MIME type observed by the service."""

    mime_type: str
    data: Any


@dataclass(frozen=True)
class _OwnedSnapshot:
    owner_subject: str
    content: str


@dataclass(frozen=True)
class _OwnedArtifact:
    owner_subject: str
    payload: ArtifactPayload


def is_valid_png(payload: Any) -> bool:
    """Return whether payload is a non-empty, bounded PNG byte sequence."""

    return (
        isinstance(payload, bytes)
        and len(PNG_SIGNATURE) <= len(payload) <= MAX_PREVIEW_BYTES
        and payload.startswith(PNG_SIGNATURE)
    )


class _BackendRuntime:
    """Typed Phase 0 reads plus the compatibility fallback used by fixture writes."""

    def __init__(self, backend: EzdxfBackend) -> None:
        self.backend = backend

    async def get_status(self) -> CommandResult:
        return await self.backend.status()

    async def health(self) -> CommandResult:
        return await self.backend.health()

    async def get_drawing_info(self) -> CommandResult:
        return await self.backend.drawing_info()

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        return await self.backend.entity_list(layer)

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        return await self.backend.entity_get(entity_id)

    async def list_layers(self) -> CommandResult:
        return await self.backend.layer_list()

    async def get_screenshot(self) -> CommandResult:
        return await self.backend.get_screenshot()

    async def call(self, operation: str, *args: Any) -> CommandResult:
        """Compatibility fallback for fixture writes not typed in Phase 1.1."""
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self) -> CommandResult:
        return await self.backend.initialize()


class Phase0Services:
    """Fresh-per-test store backed by a real, headless EzdxfBackend fixture."""

    def __init__(
        self,
        backend: EzdxfBackend | None = None,
        *,
        application_service: CadApplicationService | None = None,
    ) -> None:
        self.backend = backend or EzdxfBackend()
        self.runtime = _BackendRuntime(self.backend)
        self.application_service = application_service or CadApplicationService(
            runtime=self.runtime
        )
        self.calls: list[dict[str, str]] = []
        self.force_backend_error = False
        self.raise_unexpected = False
        self._fixture_preview = ArtifactPayload(mime_type="image/png", data=b"")
        self._snapshots: dict[str, _OwnedSnapshot] = {}
        self._artifacts: dict[str, _OwnedArtifact] = {}
        self._initialized = False

    @property
    def materialized_snapshot_count(self) -> int:
        return len(self._snapshots)

    async def _required_fixture_step(
        self,
        invocation: CadInvocation,
        stage: str,
    ):
        response = await self.application_service.execute(invocation)
        if not response.result.ok:
            raise RuntimeError(f"DXF fixture initialization failed at {stage}")
        return response

    async def initialize(self) -> None:
        self._initialized = False
        self._snapshots.clear()
        self._artifacts.clear()

        await self._required_fixture_step(
            CadInvocation(group="system", operation="init", arguments={}),
            "backend initialization",
        )
        await self._required_fixture_step(
            CadInvocation(
                group="entity",
                operation="create_line",
                arguments={"x1": 0, "y1": 0, "x2": 100, "y2": 0},
            ),
            "LINE creation",
        )
        await self._required_fixture_step(
            CadInvocation(
                group="entity",
                operation="create_circle",
                arguments={"data": {"cx": 50, "cy": 25, "radius": 10}},
            ),
            "CIRCLE creation",
        )
        screenshot = await self.application_service.get_screenshot()
        if not screenshot.result.ok:
            raise RuntimeError("DXF fixture initialization failed at preview rendering")
        png_attachment = next(
            (item for item in screenshot.attachments if item.mime_type == "image/png"),
            None,
        )
        if png_attachment is None:
            raise RuntimeError("DXF fixture initialization failed at preview validation")
        try:
            preview_bytes = base64.b64decode(png_attachment.data, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise RuntimeError("DXF fixture initialization failed at preview validation") from None
        if not is_valid_png(preview_bytes):
            raise RuntimeError("DXF fixture initialization failed at preview validation")
        self._fixture_preview = ArtifactPayload(mime_type="image/png", data=preview_bytes)
        self._initialized = True

    def _record(self, operation: str, principal: Principal, correlation_id: str) -> None:
        self.calls.append(
            {
                "operation": operation,
                "subject": principal.subject,
                "correlation_id": correlation_id,
                "scopes": ",".join(principal.scopes),
            }
        )

    def _forced_failure(self) -> CommandResult | None:
        if self.raise_unexpected:
            raise RuntimeError("fixture failure with an implementation path")
        if self.force_backend_error:
            return CommandResult(
                ok=False,
                error="backend fixture failed at an internal path",
                error_code="backend_error",
            )
        if not self._initialized:
            return CommandResult(ok=False, error="fixture is not initialized", error_code="backend_error")
        return None

    @staticmethod
    def _safe_backend_failure() -> CommandResult:
        return CommandResult(
            ok=False,
            error="backend observation failed",
            error_code="backend_error",
        )

    @staticmethod
    def _safe_string_list(value: Any, *, limit: int = 256) -> list[str] | None:
        if not isinstance(value, list) or len(value) > limit:
            return None
        if not all(isinstance(item, str) and len(item) <= 255 for item in value):
            return None
        return value

    async def list_devices(
        self,
        request: CadListDevicesInput,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_list_devices", principal, correlation_id)
        failure = self._forced_failure()
        if failure:
            return failure
        devices = [
            DeviceInfo(
                device_id="cad-online-01",
                display_name="AutoCAD fixture online",
                status="online",
                capabilities=["observe", "screenshot"],
            ),
            DeviceInfo(
                device_id="cad-offline-01",
                display_name="AutoCAD fixture offline",
                status="offline",
                capabilities=["observe"],
            ),
        ]
        if request.online_only:
            devices = [device for device in devices if device.status == "online"]
        if request.capability:
            devices = [device for device in devices if request.capability in device.capabilities]
        output = CadListDevicesOutput(
            correlation_id=correlation_id,
            devices=devices,
            default_device_id="cad-online-01" if devices else None,
        )
        return CommandResult(ok=True, payload=output.model_dump(mode="json"))

    async def _observe_backend(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        drawing = await self.application_service.get_drawing_info()
        if not drawing.ok or not isinstance(drawing.payload, dict):
            return None
        entities = await self.application_service.list_entities()
        if not entities.ok or not isinstance(entities.payload, dict):
            return None
        return drawing.payload, entities.payload

    def _build_snapshot(
        self,
        *,
        device_id: str,
        correlation_id: str,
        drawing: dict[str, Any],
        entity_query: dict[str, Any],
    ) -> tuple[str, str, str] | None:
        raw_entities = entity_query.get("entities")
        drawing_count = drawing.get("entity_count")
        query_count = entity_query.get("count")
        if not isinstance(raw_entities, list):
            return None
        if not isinstance(drawing_count, int) or isinstance(drawing_count, bool):
            return None
        if not isinstance(query_count, int) or isinstance(query_count, bool):
            return None
        if drawing_count != query_count or query_count != len(raw_entities):
            return None

        entity_types: list[str] = []
        for entity in raw_entities:
            if not isinstance(entity, dict):
                return None
            entity_type = entity.get("type")
            if not isinstance(entity_type, str) or not entity_type:
                return None
            entity_types.append(entity_type)

        layers = self._safe_string_list(drawing.get("layers"))
        blocks = self._safe_string_list(drawing.get("blocks"))
        dxf_version = drawing.get("dxf_version")
        if layers is None or blocks is None or not isinstance(dxf_version, str):
            return None

        observed_state = {
            "device_id": device_id,
            "entity_count": drawing_count,
            "entity_summary": dict(sorted(Counter(entity_types).items())),
            "layers": layers,
            "blocks": blocks,
            "dxf_version": dxf_version,
        }
        canonical_state = json.dumps(
            observed_state,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        revision = hashlib.sha256(canonical_state.encode("utf-8")).hexdigest()
        snapshot_id = f"snapshot-{device_id}-{correlation_id}"
        summary = {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": snapshot_id,
            "document_revision": revision,
            **observed_state,
        }
        return snapshot_id, revision, json.dumps(summary, ensure_ascii=True, sort_keys=True)

    async def observe(
        self,
        request: CadObserveInput,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_observe", principal, correlation_id)
        failure = self._forced_failure()
        if failure:
            return failure
        if request.device_id not in {"cad-online-01", "cad-offline-01"}:
            return CommandResult(ok=False, error="device does not exist", error_code="not_found")

        observed = await self._observe_backend()
        if observed is None:
            return self._safe_backend_failure()
        snapshot_data = self._build_snapshot(
            device_id=request.device_id,
            correlation_id=correlation_id,
            drawing=observed[0],
            entity_query=observed[1],
        )
        if snapshot_data is None:
            return self._safe_backend_failure()

        snapshot_id, revision, snapshot_json = snapshot_data
        artifact_id = f"artifact-{snapshot_id}-preview"
        output = CadObserveOutput(
            correlation_id=correlation_id,
            device_id=request.device_id,
            snapshot_id=snapshot_id,
            document_revision=revision,
            summary_uri=f"cad://snapshots/{snapshot_id}/summary",
            artifact_refs=[
                ArtifactRef(
                    artifact_id=artifact_id,
                    uri=f"cad://artifacts/{artifact_id}",
                    mime_type="image/png",
                )
            ],
        )

        # Materialize only after both backend observations and output validation succeed.
        self._snapshots[snapshot_id] = _OwnedSnapshot(principal.subject, snapshot_json)
        self._artifacts[artifact_id] = _OwnedArtifact(principal.subject, self._fixture_preview)
        return CommandResult(ok=True, payload=output.model_dump(mode="json"))

    async def get_job(
        self,
        request: CadGetJobInput,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_get_job", principal, correlation_id)
        failure = self._forced_failure()
        if failure:
            return failure
        jobs = {
            "job-completed-01": CadGetJobOutput(
                correlation_id=correlation_id,
                job_id=request.job_id,
                state="completed",
                progress=1.0,
                result={"snapshot_id": "snapshot-cad-online-01"},
                next_cursor=None,
            ),
            "job-running-01": CadGetJobOutput(
                correlation_id=correlation_id,
                job_id=request.job_id,
                state="running",
                progress=0.5,
                result=None,
                next_cursor=None if request.event_cursor == "cursor-001" else "cursor-001",
            ),
        }
        output = jobs.get(request.job_id)
        if output is None:
            return CommandResult(ok=False, error="job does not exist", error_code="not_found")
        return CommandResult(ok=True, payload=output.model_dump(mode="json"))

    async def read_snapshot(
        self,
        snapshot_id: str,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_snapshot_summary", principal, correlation_id)
        stored = self._snapshots.get(snapshot_id)
        if stored is None or stored.owner_subject != principal.subject:
            return CommandResult(ok=False, error="snapshot does not exist", error_code="not_found")
        return CommandResult(ok=True, payload=stored.content)

    async def read_artifact(
        self,
        artifact_id: str,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_artifact", principal, correlation_id)
        stored = self._artifacts.get(artifact_id)
        if stored is None or stored.owner_subject != principal.subject:
            return CommandResult(ok=False, error="artifact does not exist", error_code="not_found")
        return CommandResult(ok=True, payload=stored.payload)
