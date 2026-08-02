"""Normalize bounded runtime-neutral source entities into scene nodes."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .canonical import stable_id
from .models import (
    ArcGeometry,
    Bounds,
    CircleGeometry,
    LineGeometry,
    PolylineGeometry,
    PolylineVertex,
    SceneNode,
    finite,
    point,
)

SUPPORTED = frozenset({"LINE", "CIRCLE", "LWPOLYLINE", "ARC"})
MAX_VERTICES = 4096


def project_entities(entities: Iterable[Mapping[str, Any]]) -> tuple[SceneNode, ...]:
    nodes = [project_entity(entity) for entity in entities]
    nodes.sort(key=lambda item: item.source_entity_id.upper())
    keys = [item.source_entity_id.upper() for item in nodes]
    if len(keys) != len(set(keys)):
        raise ValueError("source entity IDs must be unique")
    return tuple(nodes)


def project_entity(raw: Mapping[str, Any]) -> SceneNode:
    entity_id = str(raw.get("entity_id", raw.get("handle", ""))).strip()
    if not entity_id:
        raise ValueError("source entity ID is required")
    entity_type = str(raw.get("entity_type", raw.get("type", ""))).strip().upper()
    node_id = stable_id(
        "nod",
        "cad.scene-node-id/1",
        {"source_entity_id": entity_id},
    )
    base = {
        "node_id": node_id,
        "source_entity_id": entity_id,
        "entity_type": entity_type or "UNKNOWN",
        "layer": str(raw.get("layer", "0"))[:255],
        "space": str(raw.get("space", "model")).lower(),
        "fingerprint": _text(raw.get("fingerprint")),
        "source_runtime": str(raw.get("source_runtime", "unknown"))[:64],
        "source_capabilities": tuple(
            sorted(str(item) for item in raw.get("source_capabilities", ()))
        ),
    }
    bounds = _bounds(raw.get("bounds"))
    requested_status = str(raw.get("geometry_status", "")).lower()
    if raw.get("geometry_truncated") or raw.get("geometry_status") == "truncated":
        return SceneNode(
            **base,
            bounds=bounds,
            geometry=None,
            geometry_status="truncated",
            geometry_reason=str(raw.get("geometry_reason", "source_geometry_truncated")),
        )
    if requested_status in {"unsupported", "unavailable", "invalid"}:
        return SceneNode(
            **base,
            bounds=bounds,
            geometry=None,
            geometry_status=requested_status,  # type: ignore[arg-type]
            geometry_reason=str(
                raw.get("geometry_reason", f"source_geometry_{requested_status}")
            ),
        )
    if entity_type not in SUPPORTED:
        return SceneNode(
            **base,
            bounds=bounds,
            geometry=None,
            geometry_status="unsupported",
            geometry_reason=str(raw.get("geometry_reason", "entity_type_unsupported")),
        )
    geometry_raw = raw.get("geometry")
    if not isinstance(geometry_raw, Mapping):
        return SceneNode(
            **base,
            bounds=bounds,
            geometry=None,
            geometry_status="unavailable",
            geometry_reason=str(raw.get("geometry_reason", "source_geometry_unavailable")),
        )
    try:
        geometry = _geometry(entity_type, geometry_raw)
        geometry_bounds = _geometry_bounds(geometry)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return SceneNode(
            **base,
            bounds=bounds,
            geometry=None,
            geometry_status="invalid",
            geometry_reason=str(error)[:128],
        )
    return SceneNode(
        **base,
        bounds=geometry_bounds,
        geometry=geometry,
        geometry_status=(
            "bounded_projection"
            if requested_status == "bounded_projection"
            else "exact"
        ),
        geometry_reason=None,
    )


def _geometry(entity_type: str, value: Mapping[str, Any]):
    if entity_type == "LINE":
        return LineGeometry(point(value["start"], "line.start"), point(value["end"], "line.end"))
    if entity_type == "CIRCLE":
        _planar_normal(value.get("normal"))
        radius = finite(value["radius"], "circle.radius")
        if radius <= 0:
            raise ValueError("circle.radius must be positive")
        return CircleGeometry(point(value["center"], "circle.center"), radius)
    if entity_type == "ARC":
        _planar_normal(value.get("normal"))
        radius = finite(value["radius"], "arc.radius")
        if radius <= 0:
            raise ValueError("arc.radius must be positive")
        return ArcGeometry(
            point(value["center"], "arc.center"),
            radius,
            _arc_angle(value, "start"),
            _arc_angle(value, "end"),
        )
    _planar_normal(value.get("normal"))
    elevation = finite(value.get("elevation", 0.0), "polyline.elevation")
    vertices_raw = value.get("vertices", value.get("points"))
    if not isinstance(vertices_raw, (list, tuple)) or not 2 <= len(vertices_raw) <= MAX_VERTICES:
        raise ValueError("polyline vertices must contain 2..4096 items")
    vertices = tuple(_vertex(item, index) for index, item in enumerate(vertices_raw))
    return PolylineGeometry(vertices, bool(value.get("closed", False)), elevation)


def _vertex(value: Any, index: int) -> PolylineVertex:
    if isinstance(value, Mapping):
        return PolylineVertex(
            finite(value["x"], f"vertices[{index}].x"),
            finite(value["y"], f"vertices[{index}].y"),
            finite(value.get("bulge", 0.0), f"vertices[{index}].bulge"),
        )
    xy = point(value, f"vertices[{index}]")
    bulge = finite(value[2], f"vertices[{index}].bulge") if len(value) > 2 else 0.0
    return PolylineVertex(*xy, bulge)


def _planar_normal(value: Any) -> None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError("planar normal evidence is required")
    x, y, z = (finite(item, "normal") for item in value[:3])
    if abs(x) > 1e-9 or abs(y) > 1e-9 or abs(abs(z) - 1.0) > 1e-9:
        raise ValueError("entity is not WCS-planar")


def _bounds(value: Any) -> Bounds | None:
    if not isinstance(value, Mapping):
        return None
    minimum, maximum = value.get("min"), value.get("max")
    if not isinstance(minimum, (list, tuple)) or not isinstance(maximum, (list, tuple)):
        return None
    try:
        return Bounds(*point(minimum, "bounds.min"), *point(maximum, "bounds.max"))
    except ValueError:
        return None


def _geometry_bounds(geometry: Any) -> Bounds:
    if isinstance(geometry, LineGeometry):
        points = (geometry.start, geometry.end)
    elif isinstance(geometry, CircleGeometry):
        x, y = geometry.center
        return Bounds(x - geometry.radius, y - geometry.radius, x + geometry.radius, y + geometry.radius)
    elif isinstance(geometry, PolylineGeometry):
        points = tuple(vertex.xy for vertex in geometry.vertices)
        # Bulged segments may extend outside their endpoints.
        arc_points = [
            point_
            for first, second in _polyline_pairs(geometry)
            if first.bulge
            for point_ in _bulge_extrema(first, second)
        ]
        points += tuple(arc_points)
    else:
        points = _arc_extrema(geometry)
    return Bounds(
        min(item[0] for item in points),
        min(item[1] for item in points),
        max(item[0] for item in points),
        max(item[1] for item in points),
    )


def _polyline_pairs(geometry: PolylineGeometry):
    pairs = list(zip(geometry.vertices, geometry.vertices[1:]))
    if geometry.closed:
        pairs.append((geometry.vertices[-1], geometry.vertices[0]))
    return pairs


def _bulge_extrema(first: PolylineVertex, second: PolylineVertex) -> tuple[tuple[float, float], ...]:
    if not first.bulge:
        return first.xy, second.xy
    center, radius, start, end = bulge_arc(first, second)
    return _arc_points(center, radius, start, end)


def bulge_arc(
    first: PolylineVertex,
    second: PolylineVertex,
) -> tuple[tuple[float, float], float, float, float]:
    dx, dy = second.x - first.x, second.y - first.y
    chord = math.hypot(dx, dy)
    if chord == 0 or first.bulge == 0:
        raise ValueError("invalid bulged segment")
    theta = 4.0 * math.atan(first.bulge)
    radius = abs(chord / (2.0 * math.sin(theta / 2.0)))
    midpoint = ((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
    offset = chord / (2.0 * math.tan(theta / 2.0))
    center = (midpoint[0] - dy / chord * offset, midpoint[1] + dx / chord * offset)
    start = math.atan2(first.y - center[1], first.x - center[0]) % math.tau
    end = math.atan2(second.y - center[1], second.x - center[0]) % math.tau
    if first.bulge < 0:
        start, end = end, start
    return center, radius, start, end


def _arc_extrema(geometry: ArcGeometry) -> tuple[Point, ...]:
    return _arc_points(
        geometry.center,
        geometry.radius,
        geometry.start_angle_radians,
        geometry.end_angle_radians,
    )


def _arc_points(center: Point, radius: float, start: float, end: float) -> tuple[Point, ...]:
    angles = [start, end]
    angles.extend(
        angle
        for angle in (0.0, math.pi / 2, math.pi, math.pi * 1.5)
        if _on_ccw_arc(angle, start, end)
    )
    return tuple(
        (
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
        )
        for angle in angles
    )


def _on_ccw_arc(angle: float, start: float, end: float) -> bool:
    return (angle - start) % math.tau <= (end - start) % math.tau


def _arc_angle(value: Mapping[str, Any], name: str) -> float:
    radians_key = f"{name}_angle_radians"
    degrees_key = f"{name}_angle_degrees"
    if radians_key in value:
        return finite(value[radians_key], f"arc.{radians_key}") % math.tau
    if degrees_key in value:
        return math.radians(finite(value[degrees_key], f"arc.{degrees_key}")) % math.tau
    raise ValueError(f"arc.{radians_key} is required")


def _text(value: Any) -> str | None:
    return None if value is None else str(value)[:255]
