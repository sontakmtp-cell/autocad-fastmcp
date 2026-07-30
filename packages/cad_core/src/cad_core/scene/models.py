"""Small immutable models for the pure Phase 10 scene engine."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

Point = tuple[float, float]
GeometryStatus = Literal[
    "exact",
    "bounded_projection",
    "truncated",
    "unsupported",
    "unavailable",
    "invalid",
]


def finite(value: float, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def point(value: Any, field: str) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(f"{field} must be a two-number point")
    return finite(value[0], field), finite(value[1], field)


@dataclass(frozen=True, slots=True)
class Bounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        values = tuple(finite(value, "bounds") for value in asdict(self).values())
        if values[0] > values[2] or values[1] > values[3]:
            raise ValueError("bounds minimum cannot exceed maximum")

    def expanded(self, amount: float) -> "Bounds":
        return Bounds(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )

    def intersects(self, other: "Bounds", tolerance: float = 0.0) -> bool:
        return not (
            self.max_x < other.min_x - tolerance
            or other.max_x < self.min_x - tolerance
            or self.max_y < other.min_y - tolerance
            or other.max_y < self.min_y - tolerance
        )

    def contains(self, other: "Bounds", tolerance: float = 0.0) -> bool:
        return (
            self.min_x - tolerance <= other.min_x
            and self.min_y - tolerance <= other.min_y
            and self.max_x + tolerance >= other.max_x
            and self.max_y + tolerance >= other.max_y
        )

    def to_dict(self) -> dict[str, list[float]]:
        return {
            "min": [self.min_x, self.min_y],
            "max": [self.max_x, self.max_y],
        }


@dataclass(frozen=True, slots=True)
class LineGeometry:
    start: Point
    end: Point


@dataclass(frozen=True, slots=True)
class CircleGeometry:
    center: Point
    radius: float


@dataclass(frozen=True, slots=True)
class PolylineVertex:
    x: float
    y: float
    bulge: float = 0.0

    @property
    def xy(self) -> Point:
        return self.x, self.y


@dataclass(frozen=True, slots=True)
class PolylineGeometry:
    vertices: tuple[PolylineVertex, ...]
    closed: bool
    elevation: float = 0.0


@dataclass(frozen=True, slots=True)
class ArcGeometry:
    center: Point
    radius: float
    start_angle_radians: float
    end_angle_radians: float


Geometry = LineGeometry | CircleGeometry | PolylineGeometry | ArcGeometry


@dataclass(frozen=True, slots=True)
class SceneNode:
    node_id: str
    source_entity_id: str
    entity_type: str
    layer: str
    space: str
    bounds: Bounds | None
    geometry: Geometry | None
    geometry_status: GeometryStatus
    geometry_reason: str | None
    fingerprint: str | None
    source_runtime: str
    source_capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bounds"] = self.bounds.to_dict() if self.bounds else None
        return value


@dataclass(frozen=True, slots=True)
class SceneRelation:
    relation_id: str
    relation_type: str
    source_node_ids: tuple[str, ...]
    directionality: Literal["symmetric", "directed"]
    evidence_strength: Literal["exact_source_geometry", "derived_exact"]
    confidence: float
    metrics: tuple[tuple[str, float], ...]
    tolerance_used: float
    algorithm_version: str = "scene-relations/1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["metrics"] = dict(self.metrics)
        return value


@dataclass(frozen=True, slots=True)
class SceneContour:
    contour_id: str
    source_node_ids: tuple[str, ...]
    closed: bool
    kind: Literal["polyline", "line_loop"]
    bounds: Bounds
    algorithm_version: str = "scene-contours/1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["bounds"] = self.bounds.to_dict()
        return value


@dataclass(frozen=True, slots=True)
class SceneComponent:
    component_id: str
    source_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SceneFeature:
    feature_id: str
    feature_type: str
    source_node_ids: tuple[str, ...]
    source_relation_ids: tuple[str, ...]
    confidence: float
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    geometry_summary: tuple[tuple[str, Any], ...] = ()
    evidence_strength: Literal["derived_exact", "bounded_heuristic"] = "derived_exact"
    algorithm_version: str = "scene-features/1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["geometry_summary"] = dict(self.geometry_summary)
        return value


@dataclass(frozen=True, slots=True)
class SceneIssue:
    issue_id: str
    code: str
    severity: Literal["info", "warning", "error"]
    source_node_ids: tuple[str, ...]
    source_relation_ids: tuple[str, ...]
    message_key: str
    evidence: tuple[str, ...]
    confidence: float
    suggested_action: str
    write_authority: bool = False
    detector_version: str = "scene-issues/1"


@dataclass(frozen=True, slots=True)
class SceneStats:
    source_entities: int
    projected_nodes: int
    spatial_cells: int
    relation_candidates: int
    relations: int
    contours: int
    components: int
    features: int
    issues: int
    projected_bytes: int
    scene_bytes: int
    build_seconds: float


@dataclass(frozen=True, slots=True)
class SceneBuildContext:
    source_snapshot_id: str
    device_id: str
    document_id: str
    document_revision: str
    space: str = "model"
    profile_id: str = "mechanical-2d/1"
    source_capabilities: tuple[str, ...] = ()
    drawing_units: str = "unitless"
    build_options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneArtifact:
    context: SceneBuildContext
    source_digest: str
    scene_digest: str
    tolerance_profile: Any
    nodes: tuple[SceneNode, ...]
    relations: tuple[SceneRelation, ...]
    contours: tuple[SceneContour, ...]
    components: tuple[SceneComponent, ...]
    features: tuple[SceneFeature, ...]
    issues: tuple[SceneIssue, ...]
    stats: SceneStats
    complete: bool = True
    engine_version: str = "scene-engine/1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_snapshot_id": self.context.source_snapshot_id,
            "device_id": self.context.device_id,
            "document_id": self.context.document_id,
            "document_revision": self.context.document_revision,
            "space": self.context.space,
            "profile_id": self.context.profile_id,
            "source_capabilities": list(self.context.source_capabilities),
            "source_digest": self.source_digest,
            "scene_digest": self.scene_digest,
            "tolerance_profile": asdict(self.tolerance_profile),
            "nodes": [item.to_dict() for item in self.nodes],
            "relations": [item.to_dict() for item in self.relations],
            "contours": [item.to_dict() for item in self.contours],
            "components": [asdict(item) for item in self.components],
            "features": [item.to_dict() for item in self.features],
            "issues": [asdict(item) for item in self.issues],
            "stats": asdict(self.stats),
            "complete": self.complete,
            "engine_version": self.engine_version,
        }
