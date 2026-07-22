"""Public v1 domain service: devices, immutable snapshots and bounded query."""

from __future__ import annotations

import base64
import copy
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from cad_core import CadApplicationService, CadInvocation

from .contracts import (
    ArtifactRef,
    CadEntity,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveOutput,
    CadQueryInput,
    CadQueryOutput,
    CONTRACT_VERSION,
    DeviceInfo,
    Principal,
)
from .snapshots import (
    SnapshotRecord,
    canonical_json,
    decode_cursor,
    document_revision,
    encode_cursor,
)


LOCAL_SUBJECT = "local-single-user"
DEFAULT_DEVICE_ID = "local-default"
MAX_IMAGE_BYTES_DEFAULT = 5 * 1024 * 1024
ALLOWED_GEOMETRY_FIELDS = frozenset(
    {
        "start",
        "end",
        "center",
        "radius",
        "points",
        "vertices",
        "major_axis",
        "ratio",
        "start_angle",
        "end_angle",
        "insert",
        "scale",
        "rotation",
    }
)
ALLOWED_DRAWING_FIELDS = frozenset(
    {"entity_count", "layers", "blocks", "dxf_version", "name", "active_document"}
)


class GatewayError(Exception):
    """Safe domain error that may cross the MCP boundary."""

    def __init__(self, code: str, message: str = "operation failed") -> None:
        self.code = code
        super().__init__(message)


@dataclass
class _BackendRuntime:
    backend: Any

    async def get_status(self):
        return await self.backend.status()

    async def health(self):
        return await self.backend.health()

    async def get_drawing_info(self):
        return await self.backend.drawing_info()

    async def list_entities(self, *, layer: str | None = None):
        return await self.backend.entity_list(layer)

    async def get_entity(self, *, entity_id: str):
        return await self.backend.entity_get(entity_id)

    async def list_layers(self):
        return await self.backend.layer_list()

    async def get_screenshot(self):
        return await self.backend.get_screenshot()

    async def call(self, operation: str, *args: Any):
        """Compatibility fallback for write/legacy operations."""
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self):
        return await self.backend.initialize()


class GatewayServices:
    """Application service with one local device and in-memory immutable snapshots."""

    def __init__(
        self,
        backend: Any,
        *,
        application_service: CadApplicationService | None = None,
        owner_subject: str = LOCAL_SUBJECT,
        dxf_path: str | None = None,
        max_image_bytes: int = MAX_IMAGE_BYTES_DEFAULT,
    ) -> None:
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be greater than zero")
        self.backend = backend
        self.application_service = application_service or CadApplicationService(
            runtime=_BackendRuntime(backend)
        )
        self.owner_subject = owner_subject
        self.dxf_path = dxf_path or os.environ.get("AUTOCAD_MCP_PUBLIC_V1_DXF_PATH", "").strip() or None
        self.max_image_bytes = max_image_bytes
        self._snapshots: dict[str, SnapshotRecord] = {}
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        result = await self.backend.initialize()
        if not result.ok:
            raise GatewayError("backend_error")
        if self.dxf_path:
            path = Path(self.dxf_path).expanduser()
            if not path.is_file():
                raise GatewayError("backend_error")
            opened = await self.backend.drawing_open(str(path))
            if not opened.ok:
                raise GatewayError("backend_error")
        self._initialized = True

    async def list_devices(
        self, request: CadListDevicesInput, principal: Principal, correlation_id: str
    ) -> CadListDevicesOutput:
        if principal.subject != self.owner_subject:
            return CadListDevicesOutput(correlation_id=correlation_id, devices=[])
        status = await self.application_service.get_status()
        if not status.ok:
            raise GatewayError("backend_error")
        payload = status.payload if isinstance(status.payload, dict) else {}
        online = bool(payload.get("has_document", True))
        capabilities = self._capabilities()
        if request.capability and request.capability not in capabilities:
            devices: list[DeviceInfo] = []
        elif request.online_only and not online:
            devices = []
        else:
            devices = [
                DeviceInfo(
                    device_id=DEFAULT_DEVICE_ID,
                    display_name=f"Local {getattr(self.backend, 'name', 'CAD')} device",
                    status="online" if online else "offline",
                    capabilities=capabilities,
                )
            ]
        return CadListDevicesOutput(
            correlation_id=correlation_id,
            devices=devices,
            default_device_id=devices[0].device_id if devices else None,
        )

    async def observe(
        self, request: CadObserveInput, principal: Principal, correlation_id: str
    ) -> CadObserveOutput:
        self._require_device(request.device_id, principal)

        drawing_result = await self.application_service.get_drawing_info()
        entities_result = await self.application_service.list_entities()
        if not drawing_result.ok or not entities_result.ok:
            raise GatewayError("backend_error")
        if not isinstance(drawing_result.payload, dict) or not isinstance(
            entities_result.payload, dict
        ):
            raise GatewayError("backend_error")
        drawing_payload = self._dict_payload(drawing_result.payload)
        entity_rows = self._dict_payload(entities_result.payload).get("entities", [])
        if not isinstance(entity_rows, list):
            raise GatewayError("backend_error")

        entities: list[dict[str, Any]] = []
        for row in entity_rows:
            if not isinstance(row, dict):
                continue
            normalized = self._normalize_entity(row)
            if request.observation_level == "detail":
                detail = await self.application_service.get_entity(
                    entity_id=normalized["entity_id"]
                )
                if not detail.ok:
                    raise GatewayError("backend_error")
                normalized = self._normalize_entity(
                    {**row, **self._dict_payload(detail.payload)}
                )
            entities.append(normalized)
        entities.sort(key=lambda item: item["entity_id"])

        public_drawing = self._normalize_drawing(drawing_payload)
        layer_result = await self.application_service.list_layers()
        if not layer_result.ok or not isinstance(layer_result.payload, dict):
            raise GatewayError("backend_error")
        layer_payload = self._dict_payload(layer_result.payload)
        public_drawing["layers"] = sorted(
            str(layer.get("name"))
            for layer in layer_payload.get("layers", [])
            if isinstance(layer, dict) and layer.get("name") is not None
        )

        preview_bytes = None
        artifact_id = None
        if request.include_preview_image:
            screenshot = await self.application_service.get_screenshot()
            if not screenshot.attachments:
                raise GatewayError("backend_error")
            try:
                preview_bytes = base64.b64decode(
                    screenshot.attachments[0].data, validate=True
                )
            except Exception:
                raise GatewayError("backend_error") from None
            if len(preview_bytes) > self.max_image_bytes:
                raise GatewayError("response_too_large")
            artifact_id = f"artifact-{uuid.uuid4()}"

        summary = Counter(item["entity_type"] for item in entities)
        revision = document_revision(public_drawing, entities)
        snapshot_id = str(uuid.uuid4())
        snapshot = SnapshotRecord(
            snapshot_id=snapshot_id,
            owner_subject=principal.subject,
            device_id=request.device_id,
            document_revision=revision,
            observation_level=request.observation_level,
            drawing=copy.deepcopy(public_drawing),
            entity_summary=dict(sorted(summary.items())),
            entities=tuple(copy.deepcopy(entities)),
            artifact_id=artifact_id,
            artifact_bytes=preview_bytes,
        )
        self._snapshots[snapshot_id] = snapshot
        artifact_refs = (
            [
                ArtifactRef(
                    artifact_id=artifact_id,
                    uri=f"cad://artifacts/{artifact_id}",
                    mime_type="image/png",
                )
            ]
            if artifact_id
            else []
        )
        return CadObserveOutput(
            correlation_id=correlation_id,
            device_id=request.device_id,
            snapshot_id=snapshot_id,
            document_revision=revision,
            observation_level=request.observation_level,
            entity_count=len(entities),
            summary_uri=f"cad://snapshots/{snapshot_id}/summary",
            entities_uri=f"cad://snapshots/{snapshot_id}/entities",
            artifact_refs=artifact_refs,
        )

    async def query(
        self, request: CadQueryInput, principal: Principal, correlation_id: str
    ) -> CadQueryOutput:
        snapshot = self._get_snapshot(request.snapshot_id, principal)
        selected = self._filtered_entities(snapshot, request.types, request.layers)
        offset = self._cursor_offset(snapshot, request)
        page = selected[offset : offset + request.limit]
        next_cursor = None
        if offset + request.limit < len(selected):
            next_cursor = encode_cursor(
                snapshot_id=snapshot.snapshot_id,
                types=request.types,
                layers=request.layers,
                offset=offset + request.limit,
            )
        resource_uri = self._entities_uri(
            snapshot.snapshot_id, request.types, request.layers, request.limit, request.cursor
        )
        return CadQueryOutput(
            correlation_id=correlation_id,
            snapshot_id=snapshot.snapshot_id,
            document_revision=snapshot.document_revision,
            entities=[CadEntity.model_validate(copy.deepcopy(entity)) for entity in page],
            total=len(selected),
            next_cursor=next_cursor,
            resource_uri=resource_uri,
        )

    async def read_device_capabilities(
        self, device_id: str, principal: Principal
    ) -> str:
        self._require_device(device_id, principal)
        status = await self.application_service.get_status()
        if not status.ok:
            raise GatewayError("backend_error")
        payload = status.payload if isinstance(status.payload, dict) else {}
        output = {
            "contract_version": CONTRACT_VERSION,
            "device_id": device_id,
            "backend": getattr(self.backend, "name", "unknown"),
            "status": "online" if bool(payload.get("has_document", True)) else "offline",
            "capabilities": self._capabilities(),
        }
        return canonical_json(output)

    async def read_snapshot_summary(self, snapshot_id: str, principal: Principal) -> str:
        return canonical_json(self._get_snapshot(snapshot_id, principal).summary())

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
        request = CadQueryInput(
            snapshot_id=snapshot_id,
            types=types or [],
            layers=layers or [],
            cursor=cursor,
            limit=limit,
        )
        result = await self.query(
            request, principal, correlation_id or str(uuid.uuid4())
        )
        return result.model_dump_json()

    async def read_artifact(self, artifact_id: str, principal: Principal) -> bytes:
        for snapshot in self._snapshots.values():
            if snapshot.artifact_id == artifact_id:
                if snapshot.owner_subject != principal.subject:
                    raise GatewayError("not_found")
                if snapshot.artifact_bytes is None:
                    raise GatewayError("not_found")
                return bytes(snapshot.artifact_bytes)
        raise GatewayError("not_found")

    def _require_device(self, device_id: str, principal: Principal) -> None:
        if principal.subject != self.owner_subject or device_id != DEFAULT_DEVICE_ID:
            raise GatewayError("not_found")

    def _get_snapshot(self, snapshot_id: str, principal: Principal) -> SnapshotRecord:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None or snapshot.owner_subject != principal.subject:
            raise GatewayError("not_found")
        return snapshot

    def _capabilities(self) -> list[str]:
        capabilities = ["observe"]
        backend_capabilities = getattr(self.backend, "capabilities", None)
        if backend_capabilities is not None and getattr(
            backend_capabilities, "can_query_entities", False
        ):
            capabilities.append("query")
        if backend_capabilities is not None and getattr(
            backend_capabilities, "can_screenshot", False
        ):
            capabilities.append("screenshot")
        return capabilities

    @staticmethod
    def _dict_payload(payload: Any) -> dict[str, Any]:
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _normalize_drawing(payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ALLOWED_DRAWING_FIELDS:
            value = payload.get(key)
            if key in {"layers", "blocks"}:
                if isinstance(value, list):
                    result[key] = sorted(str(item) for item in value)
            elif value is not None and not isinstance(value, (dict, bytes)):
                result[key] = copy.deepcopy(value)
        return result

    @staticmethod
    def _normalize_entity(row: dict[str, Any]) -> dict[str, Any]:
        entity_id = row.get("entity_id", row.get("handle"))
        entity_type = row.get("entity_type", row.get("type"))
        if not isinstance(entity_id, str) or not entity_id:
            raise GatewayError("backend_error")
        if not isinstance(entity_type, str) or not entity_type:
            raise GatewayError("backend_error")
        geometry: dict[str, Any] = {}
        for key in ALLOWED_GEOMETRY_FIELDS:
            if key in row:
                geometry[key] = GatewayServices._json_value(row[key])
        layer = row.get("layer", "0")
        return {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "layer": str(layer),
            "geometry": geometry,
        }

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [GatewayServices._json_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): GatewayServices._json_value(item)
                for key, item in value.items()
            }
        return str(value)

    @staticmethod
    def _filtered_entities(
        snapshot: SnapshotRecord, types: list[str], layers: list[str]
    ) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(entity)
            for entity in snapshot.entities
            if (not types or entity["entity_type"] in types)
            and (not layers or entity["layer"] in layers)
        ]

    @staticmethod
    def _cursor_offset(snapshot: SnapshotRecord, request: CadQueryInput) -> int:
        if not request.cursor:
            return 0
        try:
            value = decode_cursor(request.cursor)
        except Exception:
            raise GatewayError("invalid_request") from None
        if (
            value.get("snapshot_id") != snapshot.snapshot_id
            or value.get("types") != request.types
            or value.get("layers") != request.layers
        ):
            raise GatewayError("invalid_request")
        return value["offset"]

    @staticmethod
    def _entities_uri(
        snapshot_id: str,
        types: list[str],
        layers: list[str],
        limit: int,
        cursor: str | None,
    ) -> str:
        params: list[tuple[str, str]] = [("limit", str(limit))]
        if types:
            params.append(("types", ",".join(types)))
        if layers:
            params.append(("layers", ",".join(layers)))
        if cursor:
            params.append(("cursor", cursor))
        return f"cad://snapshots/{snapshot_id}/entities?{urlencode(params)}"
