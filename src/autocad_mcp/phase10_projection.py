"""Bounded ezdxf adapter for the Phase 10 Tier A source projection."""

from __future__ import annotations

import math
from typing import Any, Iterable


_CAPABILITIES = {
    "LINE": "entity.geometry.line/1",
    "CIRCLE": "entity.geometry.circle/1",
    "LWPOLYLINE": "entity.geometry.polyline/1",
    "ARC": "entity.geometry.arc/1",
}
MAX_PHASE10_ENTITIES = 5_000
MAX_POLYLINE_VERTICES = 4_096


def project_ezdxf_entities(
    entities: Iterable[Any], *, limit: int = MAX_PHASE10_ENTITIES
) -> list[dict[str, Any]]:
    """Project at most the accepted lab limit without granting live-DWG authority."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PHASE10_ENTITIES:
        raise ValueError("limit must be between 1 and 5000")
    result: list[dict[str, Any]] = []
    for entity in entities:
        if len(result) >= limit:
            break
        result.append(project_ezdxf_entity(entity))
    return result


def project_ezdxf_entity(entity: Any) -> dict[str, Any]:
    entity_type = str(entity.dxftype()).upper()
    capability = _CAPABILITIES.get(entity_type)
    geometry: dict[str, Any] | None = None
    status = "unsupported"
    reason: str | None = "entity_type_unsupported"
    try:
        if entity_type == "LINE":
            start, end = entity.dxf.start, entity.dxf.end
            geometry = {
                "start": _xy(start),
                "end": _xy(end),
                "start_elevation": float(start.z),
                "end_elevation": float(end.z),
            }
        elif entity_type in {"CIRCLE", "ARC"}:
            center = entity.dxf.center
            geometry = {
                "center": _xy(center),
                "radius": _finite(entity.dxf.radius),
                "elevation": float(center.z),
                "normal": _xyz(entity.dxf.extrusion),
            }
            if entity_type == "ARC":
                geometry.update(
                    start_angle=_finite(entity.dxf.start_angle) * math.pi / 180.0,
                    end_angle=_finite(entity.dxf.end_angle) * math.pi / 180.0,
                )
        elif entity_type == "LWPOLYLINE":
            points = list(entity.get_points("xyb"))
            if len(points) > MAX_POLYLINE_VERTICES:
                return _result(entity, None, "truncated", "vertex_limit_exceeded", capability)
            geometry = {
                "points": [[_finite(point[0]), _finite(point[1])] for point in points],
                "bulges": [_finite(point[2]) for point in points],
                "closed": bool(entity.closed),
                "elevation": _finite(entity.dxf.elevation),
                "normal": _xyz(entity.dxf.extrusion),
            }
        if geometry is not None:
            status, reason = "exact", None
    except (AttributeError, IndexError, TypeError, ValueError):
        geometry, status, reason = None, "invalid", "non_finite_geometry"
    return _result(entity, geometry, status, reason, capability)


def _result(
    entity: Any,
    geometry: dict[str, Any] | None,
    status: str,
    reason: str | None,
    capability: str | None,
) -> dict[str, Any]:
    return {
        "handle": str(entity.dxf.get("handle", "")),
        "type": str(entity.dxftype()).upper(),
        "layer": str(entity.dxf.get("layer", "0")),
        "space": "model",
        "geometry": geometry,
        "geometry_status": status,
        "geometry_reason": reason,
        "source_capabilities": [capability] if capability else [],
        "geometry_truncated": status == "truncated",
        "source_runtime": "ezdxf_headless",
        "live_dwg_authority": False,
    }


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite geometry")
    return result


def _xy(value: Any) -> list[float]:
    return [_finite(value.x), _finite(value.y)]


def _xyz(value: Any) -> list[float]:
    return [_finite(value.x), _finite(value.y), _finite(value.z)]
