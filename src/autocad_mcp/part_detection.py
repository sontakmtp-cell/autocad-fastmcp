"""Detect independent 2D parts and resolve geometry selection targets.

The module is deliberately backend-neutral.  Backends convert their entities to
``EntityRecord`` objects, then the same stable clustering and selection rules are
used by ``annotation_detect_parts`` and automatic dimensioning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


_DEFAULT_GEOMETRY_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE"}
)


@dataclass(frozen=True)
class Bounds:
    """Normalized axis-aligned 2D bounds."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        values = (self.min_x, self.min_y, self.max_x, self.max_y)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("region coordinates must be finite numbers")
        if self.min_x > self.max_x or self.min_y > self.max_y:
            raise ValueError("region min coordinates cannot exceed max coordinates")

    @classmethod
    def from_data(cls, value: object, *, field: str = "region") -> "Bounds":
        if isinstance(value, Bounds):
            return value
        if isinstance(value, Mapping):
            if "min" in value and "max" in value:
                minimum = _xy_pair(value["min"], f"{field}.min")
                maximum = _xy_pair(value["max"], f"{field}.max")
                return cls(minimum[0], minimum[1], maximum[0], maximum[1])
            keys = ("min_x", "min_y", "max_x", "max_y")
            if all(key in value for key in keys):
                return cls(*(float(value[key]) for key in keys))
            keys = ("x1", "y1", "x2", "y2")
            if all(key in value for key in keys):
                x1, y1, x2, y2 = (float(value[key]) for key in keys)
                return cls(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if len(value) == 4:
                x1, y1, x2, y2 = (float(item) for item in value)
                return cls(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        raise ValueError(
            f"{field} must be [x1, y1, x2, y2] or an object with min/max points"
        )

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2)

    def union(self, other: "Bounds") -> "Bounds":
        return Bounds(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
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
            other.min_x >= self.min_x - tolerance
            and other.min_y >= self.min_y - tolerance
            and other.max_x <= self.max_x + tolerance
            and other.max_y <= self.max_y + tolerance
        )

    def to_dict(self) -> dict[str, list[float]]:
        return {
            "min": [self.min_x, self.min_y],
            "max": [self.max_x, self.max_y],
        }


@dataclass(frozen=True)
class EntityRecord:
    """Geometry metadata plus the backend-native entity in ``geometry``."""

    handle: str
    entity_type: str
    layer: str
    bbox: Bounds
    geometry: Any = None

    @classmethod
    def from_data(cls, value: "EntityRecord | Mapping[str, Any]") -> "EntityRecord":
        if isinstance(value, EntityRecord):
            return value
        handle = str(value.get("handle", "")).strip()
        if not handle:
            raise ValueError("entity record handle cannot be empty")
        entity_type = str(value.get("entity_type", value.get("type", ""))).strip().upper()
        if not entity_type:
            raise ValueError(f"entity {handle} type cannot be empty")
        return cls(
            handle=handle,
            entity_type=entity_type,
            layer=str(value.get("layer", "0")),
            bbox=Bounds.from_data(value.get("bbox"), field=f"entity {handle} bbox"),
            geometry=value.get("geometry"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "type": self.entity_type,
            "layer": self.layer,
            "bbox": self.bbox.to_dict(),
        }


@dataclass(frozen=True)
class DetectedPart:
    """One independent geometry cluster, numbered in stable drawing order."""

    part_id: str
    bbox: Bounds
    entities: tuple[EntityRecord, ...]

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(entity.handle for entity in self.entities)

    def to_dict(self) -> dict[str, Any]:
        type_counts: dict[str, int] = {}
        for entity in self.entities:
            type_counts[entity.entity_type] = type_counts.get(entity.entity_type, 0) + 1
        return {
            "part_id": self.part_id,
            "bbox": self.bbox.to_dict(),
            "center": list(self.bbox.center),
            "entity_count": len(self.entities),
            "entity_ids": list(self.entity_ids),
            "entity_types": type_counts,
        }


@dataclass(frozen=True)
class GeometrySelection:
    """Exactly one optional way to restrict the geometry being dimensioned."""

    target_part_id: str | None = None
    region: Bounds | None = None
    entity_ids: tuple[str, ...] = ()
    region_mode: str = "intersect"
    use_current_selection: bool = False

    @classmethod
    def from_data(cls, data: Mapping[str, Any] | None) -> "GeometrySelection":
        raw = data or {}
        target_part_id = _optional_text(raw.get("target_part_id"))
        region_value = _aliased_value(raw, "region", "target_region")
        entity_ids_value = _aliased_value(raw, "entity_ids", "target_entity_ids")
        selection_name = str(raw.get("selection", "")).strip().lower()
        use_current_selection = _selection_bool(
            raw.get("use_current_selection", raw.get("current_selection")),
            default=selection_name == "current",
        )
        if selection_name not in {"", "current"}:
            raise ValueError("selection must be 'current' when provided")

        region = (
            Bounds.from_data(region_value, field="region")
            if region_value is not None
            else None
        )
        entity_ids = _entity_id_list(entity_ids_value)
        selector_count = sum(
            (
                target_part_id is not None,
                region is not None,
                bool(entity_ids),
                use_current_selection,
            )
        )
        if selector_count > 1:
            raise ValueError(
                "Use only one geometry selector: target_part_id, region, entity_ids, or current selection"
            )

        region_mode = str(raw.get("region_mode", "intersect")).strip().lower()
        if region_mode not in {"intersect", "contained"}:
            raise ValueError("region_mode must be 'intersect' or 'contained'")
        return cls(
            target_part_id=target_part_id,
            region=region,
            entity_ids=entity_ids,
            region_mode=region_mode,
            use_current_selection=use_current_selection,
        )

    @property
    def is_active(self) -> bool:
        return bool(
            self.target_part_id
            or self.region
            or self.entity_ids
            or self.use_current_selection
        )


def detect_parts(
    records: Iterable[EntityRecord | Mapping[str, Any]],
    *,
    gap_tolerance: float | None = None,
) -> list[DetectedPart]:
    """Cluster touching/overlapping entity bounds into stable numbered parts.

    A second component-level pass attaches enclosed features (for example holes)
    to a boundary assembled from separate LINE entities.
    """

    normalized = _normalize_records(records)
    if not normalized:
        return []
    if gap_tolerance is not None and (
        not math.isfinite(gap_tolerance) or gap_tolerance < 0
    ):
        raise ValueError("gap_tolerance must be a finite non-negative number")

    drawing_bounds = _combined_bounds(record.bbox for record in normalized)
    scale = max(
        drawing_bounds.max_x - drawing_bounds.min_x,
        drawing_bounds.max_y - drawing_bounds.min_y,
        1.0,
    )
    tolerance = gap_tolerance if gap_tolerance is not None else scale * 1e-9

    components: list[list[EntityRecord]] = [[record] for record in normalized]
    components = _merge_overlapping_components(components, tolerance)
    components.sort(key=_component_sort_key)

    parts: list[DetectedPart] = []
    for index, component in enumerate(components, start=1):
        component.sort(key=lambda item: (_handle_sort_key(item.handle), item.entity_type))
        parts.append(
            DetectedPart(
                part_id=f"part_{index}",
                bbox=_combined_bounds(record.bbox for record in component),
                entities=tuple(component),
            )
        )
    return parts


def select_records(
    records: Iterable[EntityRecord | Mapping[str, Any]],
    selection: GeometrySelection | Mapping[str, Any] | None,
    *,
    parts: Iterable[DetectedPart] | None = None,
) -> list[EntityRecord]:
    """Resolve one selection contract to backend-native geometry records."""

    normalized = _normalize_records(records)
    resolved = (
        selection
        if isinstance(selection, GeometrySelection)
        else GeometrySelection.from_data(selection)
    )
    if not resolved.is_active:
        return normalized
    if resolved.use_current_selection:
        # The backend exporter has already resolved AutoCAD's PICKFIRST set.
        return normalized

    if resolved.target_part_id:
        detected = list(parts) if parts is not None else detect_parts(normalized)
        for part in detected:
            if part.part_id == resolved.target_part_id:
                wanted = {handle.upper() for handle in part.entity_ids}
                return [record for record in normalized if record.handle.upper() in wanted]
        available = ", ".join(part.part_id for part in detected) or "none"
        raise ValueError(
            f"Unknown target_part_id {resolved.target_part_id!r}; available parts: {available}"
        )

    if resolved.region:
        if resolved.region_mode == "contained":
            return [
                record
                for record in normalized
                if resolved.region.contains(record.bbox)
            ]
        return [
            record
            for record in normalized
            if resolved.region.intersects(record.bbox)
        ]

    wanted = {handle.upper() for handle in resolved.entity_ids}
    available = {record.handle.upper() for record in normalized}
    missing = sorted(wanted - available, key=_handle_sort_key)
    if missing:
        raise ValueError(f"Unknown entity_ids: {', '.join(missing)}")
    return [record for record in normalized if record.handle.upper() in wanted]


def records_from_ezdxf(
    entities: Iterable[Any],
    *,
    excluded_layers: Iterable[str] = (),
    source_layers: Iterable[str] = (),
    supported_types: Iterable[str] = _DEFAULT_GEOMETRY_TYPES,
) -> list[EntityRecord]:
    """Convert supported ezdxf Model Space entities to generic records."""

    from ezdxf import bbox as ezdxf_bbox

    excluded = {str(layer).upper() for layer in excluded_layers}
    allowed = {str(layer).upper() for layer in source_layers}
    supported = {str(entity_type).upper() for entity_type in supported_types}
    records: list[EntityRecord] = []

    for entity in entities:
        entity_type = str(entity.dxftype()).upper()
        layer = str(entity.dxf.get("layer", "0"))
        if entity_type not in supported or layer.upper() in excluded:
            continue
        if allowed and layer.upper() not in allowed:
            continue
        handle = str(entity.dxf.get("handle", "")).strip()
        if not handle:
            continue
        extent = ezdxf_bbox.extents([entity], fast=True)
        if not extent.has_data:
            continue
        records.append(
            EntityRecord(
                handle=handle,
                entity_type=entity_type,
                layer=layer,
                bbox=Bounds(
                    float(extent.extmin.x),
                    float(extent.extmin.y),
                    float(extent.extmax.x),
                    float(extent.extmax.y),
                ),
                geometry=entity,
            )
        )
    return records


def _merge_overlapping_components(
    components: list[list[EntityRecord]],
    tolerance: float,
) -> list[list[EntityRecord]]:
    while True:
        merged = False
        result: list[list[EntityRecord]] = []
        for component in components:
            component_bounds = _combined_bounds(record.bbox for record in component)
            for index, existing in enumerate(result):
                existing_bounds = _combined_bounds(record.bbox for record in existing)
                if component_bounds.intersects(existing_bounds, tolerance):
                    result[index] = existing + component
                    merged = True
                    break
            else:
                result.append(component)
        components = result
        if not merged:
            return components


def _normalize_records(
    records: Iterable[EntityRecord | Mapping[str, Any]],
) -> list[EntityRecord]:
    normalized = [EntityRecord.from_data(record) for record in records]
    seen: set[str] = set()
    for record in normalized:
        key = record.handle.upper()
        if key in seen:
            raise ValueError(f"Duplicate entity handle: {record.handle}")
        seen.add(key)
    return normalized


def _combined_bounds(bounds: Iterable[Bounds]) -> Bounds:
    iterator = iter(bounds)
    try:
        combined = next(iterator)
    except StopIteration as exc:
        raise ValueError("Cannot combine an empty bounds collection") from exc
    for item in iterator:
        combined = combined.union(item)
    return combined


def _component_sort_key(component: list[EntityRecord]) -> tuple[float, float, str]:
    bounds = _combined_bounds(record.bbox for record in component)
    return (bounds.min_x, bounds.min_y, min(record.handle for record in component))


def _handle_sort_key(handle: str) -> tuple[int, int | str]:
    try:
        return (0, int(handle, 16))
    except ValueError:
        return (1, handle.upper())


def _xy_pair(value: object, field: str) -> tuple[float, float]:
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) >= 2
    ):
        return (float(value[0]), float(value[1]))
    raise ValueError(f"{field} must be a two-number point")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _aliased_value(raw: Mapping[str, Any], primary: str, alias: str) -> Any:
    primary_value = raw.get(primary)
    alias_value = raw.get(alias)
    if primary_value is not None and alias_value is not None:
        raise ValueError(f"Use {primary} or {alias}, not both")
    return primary_value if primary_value is not None else alias_value


def _entity_id_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("entity_ids must be an array of entity handles")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        handle = str(item).strip()
        if not handle:
            raise ValueError("entity_ids cannot contain an empty handle")
        key = handle.upper()
        if key not in seen:
            result.append(handle)
            seen.add(key)
    return tuple(result)


def _selection_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("use_current_selection must be a boolean")
