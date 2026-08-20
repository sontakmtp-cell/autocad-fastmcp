"""Compatibility policy for large read-only detail observations.

Detail observation is page-bounded at the Managed Host but aggregated by the
Desktop Agent.  This module keeps the historical 1 MiB admission ceiling for
normal Agent traffic while allowing a larger, still bounded frame only when the
payload is an ``observe(detail)`` terminal snapshot.  It also updates the C1
Gateway validator/persistence seam to accept the Phase 10 all-entity projection.

The patch is installed by ``autocad_gateway.__init__`` after the normal Gateway
modules are imported.  Keeping the compatibility seam isolated avoids widening
CAD Program/write validation or changing their durable payload limits.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import PureWindowsPath
from typing import Any

import autocad_contracts
import autocad_contracts.agent_protocol as agent_protocol

from .application.job_service import DurableJobService
from .infrastructure.agent_transport import websocket_endpoint
from .infrastructure.sqlite import repositories

LEGACY_AGENT_MESSAGE_BYTES = 1_048_576
DETAIL_OBSERVE_MESSAGE_BYTES = 16 * 1024 * 1024
DETAIL_OBSERVE_STORAGE_BYTES = 12 * 1024 * 1024
MAX_DETAIL_ENTITIES = 5_000

_INSTALLED = False
_ORIGINAL_PARSE_AGENT_MESSAGE = websocket_endpoint.parse_agent_message
_ORIGINAL_REPOSITORY_JSON = repositories._json
_ORIGINAL_DETAIL_ENTITY_VALIDATOR = DurableJobService._valid_c1_detail_entity

_BASE_ENTITY_KEYS = {
    "entity_id",
    "entity_type",
    "layer",
    "space",
    "bounds",
    "geometry",
    "geometry_truncated",
    "fingerprint",
}
_PROVENANCE_KEYS = {
    "geometry_status",
    "geometry_reason",
    "source_runtime",
    "source_capabilities",
}
_LEGACY_GEOMETRY_TYPES = {"LINE", "CIRCLE", "LWPOLYLINE", "ARC"}
_DIMENSION_KEYS = {
    "ObjectName",
    "Handle",
    "Layer",
    "DimensionStyle",
    "Measurement",
    "TextOverride",
    "TextHeight",
    "TextPosition",
    "TextRotation",
    "Color",
    "Visible",
    "Annotative",
}


def _encoded_size(value: str | bytes | dict[str, Any]) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _decoded_envelope(value: str | bytes | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _detail_result(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    snapshot = value.get("snapshot")
    return isinstance(snapshot, dict) and snapshot.get("observation_level") == "detail"


def _detail_observation_envelope(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("message_type") not in {"result", "reconcile_result"}:
        return False
    if value.get("status") == "terminal" and value.get("result_status") != "succeeded":
        return False
    if value.get("message_type") == "result" and value.get("status") != "succeeded":
        return False
    return _detail_result(value.get("result"))


def _parse_agent_message_with_detail_limit(
    value: str | bytes | dict[str, Any],
) -> Any:
    if _encoded_size(value) > LEGACY_AGENT_MESSAGE_BYTES:
        if not _detail_observation_envelope(_decoded_envelope(value)):
            raise ValueError("Agent message exceeds the protocol byte limit")
    return _ORIGINAL_PARSE_AGENT_MESSAGE(value)


def _looks_like_detail_entity_list(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    sample = value[: min(3, len(value))]
    return all(
        isinstance(item, dict)
        and {"entity_id", "entity_type", "fingerprint"} <= set(item)
        for item in sample
    )


def _repository_json_with_detail_limit(value: Any, *, limit: int = 512_000) -> str:
    if limit == 512_000 and (
        _detail_result(value) or _looks_like_detail_entity_list(value)
    ):
        limit = DETAIL_OBSERVE_STORAGE_BYTES
    return _ORIGINAL_REPOSITORY_JSON(value, limit=limit)


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _bounded_scalar(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if _finite_number(value):
        return True
    return isinstance(value, str) and len(value.encode("utf-8")) <= 4096


def _bounded_point(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) in {2, 3}
        and all(_finite_number(item) for item in value)
    )


def _valid_detail_error(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"property", "error_type", "message"}
        and isinstance(value.get("property"), str)
        and 1 <= len(value["property"]) <= 128
        and isinstance(value.get("error_type"), str)
        and 1 <= len(value["error_type"]) <= 128
        and isinstance(value.get("message"), str)
        and len(value["message"]) <= 512
    )


def _valid_dimension_geometry(entity: dict[str, Any], geometry: Any) -> bool:
    if not isinstance(geometry, dict):
        return False
    keys = set(geometry)
    if keys not in (_DIMENSION_KEYS, _DIMENSION_KEYS | {"detail_errors"}):
        return False
    if (
        not isinstance(geometry.get("ObjectName"), str)
        or not 1 <= len(geometry["ObjectName"]) <= 128
        or geometry.get("Handle") != entity.get("entity_id")
        or geometry.get("Layer") != entity.get("layer")
        or not _bounded_scalar(geometry.get("DimensionStyle"))
        or not _bounded_scalar(geometry.get("Measurement"))
        or not _bounded_scalar(geometry.get("TextOverride"))
        or not _bounded_scalar(geometry.get("TextHeight"))
        or not (
            geometry.get("TextPosition") is None
            or _bounded_point(geometry.get("TextPosition"))
        )
        or not _bounded_scalar(geometry.get("TextRotation"))
        or not _bounded_scalar(geometry.get("Color"))
        or not _bounded_scalar(geometry.get("Visible"))
        or not _bounded_scalar(geometry.get("Annotative"))
    ):
        return False
    errors = geometry.get("detail_errors")
    return errors is None or (
        isinstance(errors, list)
        and len(errors) <= 64
        and all(_valid_detail_error(item) for item in errors)
    )


def _valid_extended_detail_entity(cls: type[DurableJobService], entity: Any) -> bool:
    if not isinstance(entity, dict):
        return False
    entity_type = entity.get("entity_type")
    entity_id = entity.get("entity_id")
    if (
        isinstance(entity_type, str)
        and entity_type in _LEGACY_GEOMETRY_TYPES
        and isinstance(entity_id, str)
        and re.fullmatch(r"[0-9A-Fa-f]{1,32}", entity_id) is not None
    ):
        return bool(_ORIGINAL_DETAIL_ENTITY_VALIDATOR(entity))

    if set(entity) != _BASE_ENTITY_KEYS | _PROVENANCE_KEYS:
        return False
    if (
        not isinstance(entity_id, str)
        or (
            re.fullmatch(r"[0-9A-Fa-f]{1,32}", entity_id) is None
            and re.fullmatch(r"UNAVAILABLE-[0-9]{1,10}", entity_id) is None
        )
        or not isinstance(entity_type, str)
        or not 1 <= len(entity_type) <= 128
        or not isinstance(entity.get("layer"), str)
        or len(entity["layer"]) > 255
        or entity.get("space") not in {"model", "paper"}
        or not isinstance(entity.get("geometry_truncated"), bool)
        or not isinstance(entity.get("fingerprint"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", entity["fingerprint"]) is None
        or not (
            entity.get("bounds") is None
            or cls._valid_c1_bounds(entity.get("bounds"))
        )
        or entity.get("source_runtime") != "managed_dotnet"
    ):
        return False

    status = entity.get("geometry_status")
    reason = entity.get("geometry_reason")
    geometry = entity.get("geometry")
    capabilities = entity.get("source_capabilities")
    if (
        status
        not in {"exact", "truncated", "unsupported", "unavailable", "invalid"}
        or not isinstance(capabilities, list)
        or len(capabilities) > 16
        or any(
            not isinstance(item, str) or not 1 <= len(item) <= 64
            for item in capabilities
        )
        or (reason is not None and (not isinstance(reason, str) or not 1 <= len(reason) <= 128))
        or entity["geometry_truncated"] is not (status == "truncated")
    ):
        return False

    is_dimension = "DIMENSION" in entity_type.upper()
    if status == "exact":
        return (
            is_dimension
            and reason is None
            and capabilities == ["entity.properties.dimension/1"]
            and _valid_dimension_geometry(entity, geometry)
        )
    if status == "truncated":
        return geometry is None and reason is not None
    if status == "unavailable" and reason == "entity_read_failed":
        return (
            capabilities == []
            and isinstance(geometry, dict)
            and set(geometry) == {"detail_error"}
            and _valid_detail_error(geometry.get("detail_error"))
        )
    return geometry is None and reason is not None and capabilities == []


def _validate_extended_detail_observation(
    cls: type[DurableJobService],
    snapshot: dict[str, Any],
    *,
    revision: dict[str, Any],
    drawing: dict[str, Any],
    summary: dict[str, Any],
    managed_dotnet: bool,
) -> str | None:
    entities = snapshot.get("entities")
    truncated = summary.get("truncated")
    if (
        not managed_dotnet
        or set(revision)
        != {"revision_schema", "revision_strength", "commit_safe"}
        or revision.get("revision_schema") != "cad.revision/1"
        or revision.get("revision_strength") != "database_object_fingerprint"
        or not isinstance(revision.get("commit_safe"), bool)
        or not isinstance(truncated, bool)
        or revision["commit_safe"] is truncated
        or not isinstance(entities, list)
        or len(entities) > MAX_DETAIL_ENTITIES
        or summary
        != {
            "entity_count": len(entities),
            "detail_available": True,
            "truncated": truncated,
        }
        or any(not cls._valid_c1_detail_entity(entity) for entity in entities)
        or len(
            {
                "geometry_status" in entity
                for entity in entities
                if isinstance(entity, dict)
            }
        )
        > 1
    ):
        return "backend_error"

    document_revision = snapshot.get("document_revision")
    if (
        not isinstance(document_revision, str)
        or re.fullmatch(r"[1-9][0-9]{0,18}", document_revision) is None
    ):
        return "backend_error"
    if set(drawing) != {
        "document_id",
        "document_name",
        "database_fingerprint",
        "entity_count",
        "layers",
        "layer_count",
        "truncated",
    }:
        return "backend_error"
    document_id = drawing.get("document_id")
    database_fingerprint = drawing.get("database_fingerprint")
    document_name = drawing.get("document_name")
    layers = drawing.get("layers")
    entity_count = drawing.get("entity_count")
    layer_count = drawing.get("layer_count")
    if (
        not isinstance(document_id, str)
        or re.fullmatch(r"doc-[A-Za-z0-9._-]{1,124}", document_id) is None
        or not isinstance(database_fingerprint, str)
        or not 1 <= len(database_fingerprint) <= 128
        or not isinstance(document_name, str)
        or not document_name
        or len(document_name) > 255
        or PureWindowsPath(document_name).name != document_name
        or "/" in document_name
        or not isinstance(layers, list)
        or len(layers) > 256
        or any(not isinstance(item, str) or len(item) > 255 for item in layers)
        or isinstance(entity_count, bool)
        or not isinstance(entity_count, int)
        or entity_count < len(entities)
        or isinstance(layer_count, bool)
        or not isinstance(layer_count, int)
        or layer_count < len(layers)
        or not isinstance(drawing.get("truncated"), bool)
    ):
        return "backend_error"
    return None


def install_observe_detail_compat() -> None:
    """Install the bounded read-only detail compatibility seam once."""

    global _INSTALLED
    if _INSTALLED:
        return

    agent_protocol.MAX_WEBSOCKET_MESSAGE_BYTES = DETAIL_OBSERVE_MESSAGE_BYTES
    autocad_contracts.MAX_WEBSOCKET_MESSAGE_BYTES = DETAIL_OBSERVE_MESSAGE_BYTES
    websocket_endpoint.MAX_WEBSOCKET_MESSAGE_BYTES = DETAIL_OBSERVE_MESSAGE_BYTES
    websocket_endpoint.parse_agent_message = _parse_agent_message_with_detail_limit
    repositories._json = _repository_json_with_detail_limit
    DurableJobService._valid_c1_detail_entity = classmethod(_valid_extended_detail_entity)
    DurableJobService._validate_c1_detail_observation = classmethod(
        _validate_extended_detail_observation
    )
    _INSTALLED = True
