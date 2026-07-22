"""Public v1 application service: bounded observation and immutable local snapshots."""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import math
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from cad_core import CadApplicationService

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
    MAX_ENTITY_TYPE_LENGTH,
    MAX_LAYER_NAME_LENGTH,
    Principal,
)
from .snapshots import (
    BoundedSnapshotStore,
    SnapshotRecord,
    SnapshotStoreFull,
    canonical_json,
    cursor_filter_hash,
    decode_cursor,
    document_revision,
    encode_cursor,
)


LOCAL_SUBJECT = "local-single-user"
DEFAULT_DEVICE_ID = "local-default"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

MAX_IMAGE_BYTES_DEFAULT = 5 * 1024 * 1024
MAX_ENTITIES_DEFAULT = 1_000
MAX_ENTITY_DETAIL_CALLS_DEFAULT = 1_000
OBSERVATION_TIMEOUT_SECONDS_DEFAULT = 15.0
MAX_SNAPSHOT_BYTES_DEFAULT = 4 * 1024 * 1024
SNAPSHOT_TTL_SECONDS_DEFAULT = 15 * 60
MAX_SNAPSHOT_COUNT_DEFAULT = 128
MAX_SNAPSHOT_STORE_BYTES_DEFAULT = 64 * 1024 * 1024

MAX_IMAGE_BYTES_UPPER = 20 * 1024 * 1024
MAX_ENTITIES_UPPER = 10_000
MAX_ENTITY_DETAIL_CALLS_UPPER = 10_000
OBSERVATION_TIMEOUT_SECONDS_UPPER = 120.0
MAX_SNAPSHOT_BYTES_UPPER = 64 * 1024 * 1024
SNAPSHOT_TTL_SECONDS_UPPER = 24 * 60 * 60
MAX_SNAPSHOT_COUNT_UPPER = 10_000
MAX_SNAPSHOT_STORE_BYTES_UPPER = 512 * 1024 * 1024

MAX_JSON_DEPTH = 16
MAX_JSON_CONTAINER_ITEMS = 10_000
MAX_JSON_STRING_BYTES = 64 * 1024
MAX_JSON_KEY_BYTES = 512

ALLOWED_ENTITY_STATE_FIELDS = frozenset(
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
        "normal",
        "extrusion",
        "closed",
        "text",
        "contents",
        "plain_text",
        "measurement",
        "dimension_text",
        "block_name",
        "effective_name",
        "attributes",
        "tag",
        "value",
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
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self):
        return await self.backend.initialize()


class GatewayServices:
    """One local device with bounded, owner-scoped in-memory snapshots."""

    def __init__(
        self,
        backend: Any,
        *,
        application_service: CadApplicationService | None = None,
        owner_subject: str = LOCAL_SUBJECT,
        dxf_path: str | None = None,
        max_image_bytes: int = MAX_IMAGE_BYTES_DEFAULT,
        max_entities: int = MAX_ENTITIES_DEFAULT,
        max_entity_detail_calls: int = MAX_ENTITY_DETAIL_CALLS_DEFAULT,
        observation_timeout_seconds: float = OBSERVATION_TIMEOUT_SECONDS_DEFAULT,
        max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES_DEFAULT,
        snapshot_ttl_seconds: float = SNAPSHOT_TTL_SECONDS_DEFAULT,
        max_snapshot_count: int = MAX_SNAPSHOT_COUNT_DEFAULT,
        max_snapshot_store_bytes: int = MAX_SNAPSHOT_STORE_BYTES_DEFAULT,
        snapshot_store: BoundedSnapshotStore | None = None,
    ) -> None:
        _bounded_limit("max_image_bytes", max_image_bytes, MAX_IMAGE_BYTES_UPPER)
        _bounded_limit("max_entities", max_entities, MAX_ENTITIES_UPPER)
        _bounded_limit(
            "max_entity_detail_calls",
            max_entity_detail_calls,
            MAX_ENTITY_DETAIL_CALLS_UPPER,
        )
        _bounded_limit(
            "observation_timeout_seconds",
            observation_timeout_seconds,
            OBSERVATION_TIMEOUT_SECONDS_UPPER,
        )
        _bounded_limit("max_snapshot_bytes", max_snapshot_bytes, MAX_SNAPSHOT_BYTES_UPPER)
        _bounded_limit(
            "snapshot_ttl_seconds", snapshot_ttl_seconds, SNAPSHOT_TTL_SECONDS_UPPER
        )
        _bounded_limit("max_snapshot_count", max_snapshot_count, MAX_SNAPSHOT_COUNT_UPPER)
        _bounded_limit(
            "max_snapshot_store_bytes",
            max_snapshot_store_bytes,
            MAX_SNAPSHOT_STORE_BYTES_UPPER,
        )
        if max_snapshot_bytes > max_snapshot_store_bytes:
            raise ValueError("max_snapshot_bytes must not exceed max_snapshot_store_bytes")
        self.backend = backend
        self.application_service = application_service or CadApplicationService(
            runtime=_BackendRuntime(backend)
        )
        self.owner_subject = owner_subject
        self.dxf_path = (
            dxf_path
            or os.environ.get("AUTOCAD_MCP_PUBLIC_V1_DXF_PATH", "").strip()
            or None
        )
        self.max_image_bytes = max_image_bytes
        self.max_entities = max_entities
        self.max_entity_detail_calls = max_entity_detail_calls
        self.observation_timeout_seconds = observation_timeout_seconds
        self.max_snapshot_bytes = max_snapshot_bytes
        self.snapshot_store = snapshot_store or BoundedSnapshotStore(
            ttl_seconds=snapshot_ttl_seconds,
            max_count=max_snapshot_count,
            max_total_bytes=max_snapshot_store_bytes,
        )
        self._initialized = False

    @property
    def snapshot_count(self) -> int:
        return self.snapshot_store.snapshot_count

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

    async def shutdown(self) -> None:
        self.snapshot_store.clear()
        self._initialized = False

    async def list_devices(
        self, request: CadListDevicesInput, principal: Principal, correlation_id: str
    ) -> CadListDevicesOutput:
        if principal.subject != self.owner_subject:
            return CadListDevicesOutput(correlation_id=correlation_id, devices=[])
        online = await self._device_online()
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
        try:
            return await asyncio.wait_for(
                self._observe(request, principal, correlation_id),
                timeout=self.observation_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise GatewayError("observation_budget_exceeded") from None

    async def _observe(
        self, request: CadObserveInput, principal: Principal, correlation_id: str
    ) -> CadObserveOutput:
        self._require_device(request.device_id, principal)
        drawing_result = await self.application_service.get_drawing_info()
        entities_result = await self.application_service.list_entities()
        if not drawing_result.ok or not entities_result.ok:
            raise GatewayError("backend_error")
        drawing_payload = _require_dict(drawing_result.payload)
        entity_list_payload = _require_dict(entities_result.payload)
        entity_rows = entity_list_payload.get("entities")
        if not isinstance(entity_rows, list):
            raise GatewayError("backend_error")
        listed_count = entity_list_payload.get("count")
        if listed_count is not None and (
            isinstance(listed_count, bool)
            or not isinstance(listed_count, int)
            or listed_count != len(entity_rows)
        ):
            raise GatewayError("backend_error")
        if len(entity_rows) > self.max_entities:
            raise GatewayError("observation_too_large")
        if len(entity_rows) > self.max_entity_detail_calls:
            raise GatewayError("observation_budget_exceeded")
        reported_count = drawing_payload.get("entity_count")
        if (
            reported_count is not None
            and (isinstance(reported_count, bool) or not isinstance(reported_count, int))
        ):
            raise GatewayError("backend_error")
        if isinstance(reported_count, int) and reported_count != len(entity_rows):
            raise GatewayError("backend_error")

        revision_entities: list[dict[str, Any]] = []
        seen_entity_ids: set[str] = set()
        for row in entity_rows:
            if not isinstance(row, dict):
                raise GatewayError("backend_error")
            listed = self._normalize_entity(row)
            if listed["entity_id"] in seen_entity_ids:
                raise GatewayError("backend_error")
            seen_entity_ids.add(listed["entity_id"])
            detail = await self.application_service.get_entity(entity_id=listed["entity_id"])
            if not detail.ok or not isinstance(detail.payload, dict):
                raise GatewayError("backend_error")
            detailed = self._normalize_entity({**row, **detail.payload})
            if any(
                detailed[field] != listed[field]
                for field in ("entity_id", "entity_type", "layer")
            ):
                # A changed identity/type/layer means the drawing mutated during the
                # observation or the backend returned the wrong entity.  Either way,
                # a concurrency-safe revision cannot be produced from this response.
                raise GatewayError("backend_error")
            revision_entities.append(detailed)
        revision_entities.sort(key=lambda item: item["entity_id"])

        public_drawing = self._normalize_drawing(drawing_payload)
        layer_result = await self.application_service.list_layers()
        if not layer_result.ok:
            raise GatewayError("backend_error")
        public_drawing["layers"] = self._normalize_layers(_require_dict(layer_result.payload))

        public_entities = copy.deepcopy(revision_entities)
        if request.observation_level == "summary":
            for entity in public_entities:
                entity["geometry"] = {}

        normalized_bytes = len(
            canonical_json(
                {
                    "drawing": public_drawing,
                    "entity_summary": dict(
                        sorted(Counter(item["entity_type"] for item in public_entities).items())
                    ),
                    "snapshot_entities": public_entities,
                    "revision_entities": revision_entities,
                }
            ).encode("utf-8")
        )
        if normalized_bytes > self.max_snapshot_bytes:
            raise GatewayError("observation_too_large")

        preview_bytes: bytes | None = None
        artifact_id: str | None = None
        if request.include_preview_image:
            screenshot = await self.application_service.get_screenshot()
            preview_bytes = self._validated_png(screenshot.attachments)
            artifact_id = f"artifact-{uuid.uuid4()}"

        revision_drawing = {
            key: copy.deepcopy(value)
            for key, value in public_drawing.items()
            if key != "entity_count"
        }
        document_name = public_drawing.get("active_document") or public_drawing.get("name")
        if not document_name and self.dxf_path:
            document_name = Path(self.dxf_path).name
        revision = document_revision(
            document_identity={
                "device_id": request.device_id,
                "document_name": document_name or "active-document",
            },
            drawing=revision_drawing,
            entities=revision_entities,
        )
        snapshot_id = str(uuid.uuid4())
        summary = Counter(item["entity_type"] for item in public_entities)
        snapshot = SnapshotRecord(
            snapshot_id=snapshot_id,
            owner_subject=principal.subject,
            device_id=request.device_id,
            document_revision=revision,
            observation_level=request.observation_level,
            drawing=copy.deepcopy(public_drawing),
            entity_summary=dict(sorted(summary.items())),
            entities=tuple(copy.deepcopy(public_entities)),
            artifact_id=artifact_id,
            artifact_bytes=preview_bytes,
        )
        try:
            self.snapshot_store.add(snapshot)
        except SnapshotStoreFull:
            raise GatewayError("observation_too_large") from None

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
            entity_count=len(public_entities),
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
        if offset > len(selected):
            raise GatewayError("invalid_request")
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

    async def read_device_capabilities(self, device_id: str, principal: Principal) -> str:
        self._require_device(_public_id(device_id), principal)
        online = await self._device_online()
        return canonical_json(
            {
                "contract_version": CONTRACT_VERSION,
                "device_id": device_id,
                "backend": getattr(self.backend, "name", "unknown"),
                "status": "online" if online else "offline",
                "capabilities": self._capabilities(),
            }
        )

    async def read_snapshot_summary(self, snapshot_id: str, principal: Principal) -> str:
        return canonical_json(self._get_snapshot(_public_id(snapshot_id), principal).summary())

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
        result = await self.query(request, principal, correlation_id or str(uuid.uuid4()))
        return result.model_dump_json()

    async def read_artifact(self, artifact_id: str, principal: Principal) -> bytes:
        value = self.snapshot_store.get_artifact(_public_id(artifact_id), principal.subject)
        if value is None:
            raise GatewayError("not_found")
        return value

    def cleanup_snapshots(self) -> int:
        return self.snapshot_store.cleanup()

    def _require_device(self, device_id: str, principal: Principal) -> None:
        if principal.subject != self.owner_subject or device_id != DEFAULT_DEVICE_ID:
            raise GatewayError("not_found")

    def _get_snapshot(self, snapshot_id: str, principal: Principal) -> SnapshotRecord:
        snapshot = self.snapshot_store.get_snapshot(snapshot_id, principal.subject)
        if snapshot is None:
            raise GatewayError("not_found")
        return snapshot

    async def _device_online(self) -> bool:
        status = await self.application_service.get_status()
        if not status.ok or not isinstance(status.payload, dict):
            raise GatewayError("backend_error")
        health = await self.application_service.health()
        status_payload = status.payload
        health_payload = health.payload if health.ok and isinstance(health.payload, dict) else {}
        nested_status = health_payload.get("status")
        if isinstance(nested_status, dict):
            health_payload = {**nested_status, **health_payload}
        details = health.details if isinstance(getattr(health, "details", None), dict) else {}
        runtime = {**details, **health_payload}
        backend_name = str(getattr(self.backend, "name", "")).lower()

        has_document = status_payload.get("has_document")
        if not isinstance(has_document, bool):
            active_document = runtime.get("active_document")
            has_document = isinstance(active_document, str) and bool(active_document.strip())
        reachable_value = runtime.get("dispatcher_reachable", runtime.get("reachable"))
        if isinstance(reachable_value, bool):
            reachable = reachable_value
        else:
            reachable = health.ok and backend_name != "file_ipc"
        busy = runtime.get("busy") is True or runtime.get("autocad_idle") is False
        modal = runtime.get("modal_dialog") is True or runtime.get("modal_dialog_active") is True
        return bool(health.ok and reachable and has_document and not busy and not modal)

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
    def _normalize_drawing(payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ALLOWED_DRAWING_FIELDS:
            if key not in payload or payload[key] is None:
                continue
            value = payload[key]
            if key == "entity_count":
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise GatewayError("backend_error")
                result[key] = value
            elif key in {"layers", "blocks"}:
                if not isinstance(value, list):
                    raise GatewayError("backend_error")
                values = [_bounded_string(item, MAX_LAYER_NAME_LENGTH) for item in value]
                result[key] = sorted(set(values))
            else:
                result[key] = _json_value(value)
        return result

    @staticmethod
    def _normalize_layers(payload: dict[str, Any]) -> list[str]:
        layers = payload.get("layers")
        if not isinstance(layers, list):
            raise GatewayError("backend_error")
        values: list[str] = []
        for layer in layers:
            if not isinstance(layer, dict):
                raise GatewayError("backend_error")
            values.append(_bounded_string(layer.get("name"), MAX_LAYER_NAME_LENGTH))
        return sorted(set(values))

    @staticmethod
    def _normalize_entity(row: dict[str, Any]) -> dict[str, Any]:
        entity_id = _bounded_string(row.get("entity_id", row.get("handle")), 128)
        entity_type = _bounded_string(
            row.get("entity_type", row.get("type")), MAX_ENTITY_TYPE_LENGTH
        ).upper()
        if "layer" not in row:
            raise GatewayError("backend_error")
        layer = _bounded_string(row.get("layer"), MAX_LAYER_NAME_LENGTH)
        geometry: dict[str, Any] = {}
        nested_geometry = row.get("geometry")
        if nested_geometry is not None:
            if not isinstance(nested_geometry, dict):
                raise GatewayError("backend_error")
            for key, value in nested_geometry.items():
                if key in ALLOWED_ENTITY_STATE_FIELDS:
                    geometry[key] = _json_value(value)
        for key in ALLOWED_ENTITY_STATE_FIELDS:
            if key in row:
                geometry[key] = _json_value(row[key])
        return {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "layer": layer,
            "geometry": geometry,
        }

    def _validated_png(self, attachments: Any) -> bytes:
        if not isinstance(attachments, tuple | list):
            raise GatewayError("preview_unavailable")
        png = next(
            (
                attachment
                for attachment in attachments
                if getattr(attachment, "mime_type", None) == "image/png"
            ),
            None,
        )
        if png is None:
            raise GatewayError("preview_unavailable")
        data = getattr(png, "data", None)
        if not isinstance(data, str) or not data:
            raise GatewayError("preview_unavailable")
        if len(data) > ((self.max_image_bytes + 2) // 3) * 4 + 4:
            raise GatewayError("response_too_large")
        try:
            value = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise GatewayError("preview_unavailable") from None
        if not value or not value.startswith(PNG_SIGNATURE):
            raise GatewayError("preview_unavailable")
        if len(value) > self.max_image_bytes:
            raise GatewayError("response_too_large")
        return value

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
        except ValueError:
            raise GatewayError("invalid_request") from None
        if (
            value.get("snapshot_id") != snapshot.snapshot_id
            or value.get("filter_hash")
            != cursor_filter_hash(request.types, request.layers)
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


def _require_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GatewayError("backend_error")
    return value


def _bounded_limit(name: str, value: float, upper: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= upper:
        raise ValueError(f"{name} must be between 1 and {upper}")


def _bounded_string(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        raise GatewayError("backend_error")
    result = value.strip()
    if not result or len(result) > max_length or len(result.encode("utf-8")) > max_length * 4:
        raise GatewayError("backend_error")
    return result


def _public_id(value: Any) -> str:
    if not isinstance(value, str):
        raise GatewayError("invalid_request")
    result = value.strip()
    if (
        not result
        or len(result) > 128
        or not result[0].isalnum()
        or any(not (character.isalnum() or character in "._-") for character in result)
    ):
        raise GatewayError("invalid_request")
    return result


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise GatewayError("backend_error")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_JSON_STRING_BYTES:
            raise GatewayError("backend_error")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GatewayError("backend_error")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise GatewayError("backend_error")
        return [_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise GatewayError("backend_error")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key.encode("utf-8")) > MAX_JSON_KEY_BYTES:
                raise GatewayError("backend_error")
            result[key] = _json_value(item, depth=depth + 1)
        return result
    raise GatewayError("backend_error")
