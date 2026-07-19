"""Backend integration for part-aware, preview-first dimension workflows."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from autocad_mcp.config import LISP_DIR
from autocad_mcp.dimension_intelligence import recognize_mechanical_features
from autocad_mcp.dimension_plans import DimensionPlan, PlannedDimension
from autocad_mcp.dimension_profiles import DimensionProfile
from autocad_mcp.part_detection import Bounds, EntityRecord, records_from_ezdxf


GEOMETRY_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "INSERT"}
)


def geometry_only(records: Iterable[EntityRecord]) -> list[EntityRecord]:
    return [record for record in records if record.entity_type in GEOMETRY_TYPES]


def drawing_fingerprint(backend: Any) -> str:
    """Identify the active document so a preview cannot be committed elsewhere."""

    if backend.name == "ezdxf":
        document = backend._doc  # noqa: SLF001
        if document is None:
            raise RuntimeError("No document open")
        return f"ezdxf:{id(document)}"
    if backend.name == "file_ipc":
        state = backend._inspect_runtime()  # noqa: SLF001
        if state.error_code or state.snapshot is None:
            raise RuntimeError(state.error or "AutoCAD has no active document")
        return f"file_ipc:{state.snapshot.identity}"
    raise RuntimeError(f"Dimension workflow is not supported by backend {backend.name!r}")


def records_fingerprint(records: Iterable[EntityRecord]) -> str:
    """Hash selected entity identity and geometry to reject stale previews."""

    payload = []
    for record in sorted(records, key=lambda item: item.handle.upper()):
        payload.append(
            {
                "handle": record.handle.upper(),
                "type": record.entity_type,
                "layer": record.layer,
                "bbox": record.bbox.to_dict(),
                "detail": _record_detail(record),
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def collect_dimension_records(
    backend: Any,
    *,
    dimension_layer: str,
    source_layers: Iterable[str] = (),
    include_dimensions: bool = False,
    use_current_selection: bool = False,
) -> list[EntityRecord]:
    """Read geometry in a common shape without changing the drawing."""

    if backend.name == "ezdxf":
        if use_current_selection:
            raise ValueError("Current AutoCAD selection is available only with the File IPC backend")
        if include_dimensions:
            return _records_from_ezdxf_with_dimensions(backend._msp)  # noqa: SLF001
        return records_from_ezdxf(
            backend._msp,  # noqa: SLF001
            excluded_layers={dimension_layer, "DEFPOINTS"},
            source_layers=source_layers,
            supported_types=GEOMETRY_TYPES,
        )
    if backend.name != "file_ipc":
        raise RuntimeError(f"Dimension workflow is not supported by backend {backend.name!r}")

    lisp_path = (LISP_DIR / "auto_dimension.lsp").resolve()
    if not lisp_path.exists():
        raise RuntimeError(f"Automatic dimension LISP file is missing: {lisp_path}")
    report_path = Path(backend._ipc_dir) / (  # noqa: SLF001
        f"autocad_mcp_dim_geometry_{uuid.uuid4().hex[:12]}.json"
    )
    excluded_layer = "__MCP_AUDIT_INCLUDE_DIMENSIONS__" if include_dimensions else dimension_layer
    try:
        result = await backend.annotation_export_dimension_geometry(
            lisp_path=str(lisp_path),
            report_path=str(report_path),
            dimension_layer=excluded_layer,
            source_layers=";".join(str(layer) for layer in source_layers),
            use_current_selection=use_current_selection,
        )
        if not result.ok:
            raise RuntimeError(result.error or "AutoCAD geometry export failed")
        if not report_path.exists():
            raise RuntimeError("AutoCAD did not produce the dimension geometry report")
        payload = _read_json_file(report_path)
        if payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error", "AutoCAD geometry export failed")))
        return [EntityRecord.from_data(item) for item in payload.get("entities", [])]
    finally:
        try:
            report_path.unlink(missing_ok=True)
        except OSError:
            pass


def build_dimension_candidates(
    records: Iterable[EntityRecord],
    *,
    options: Any,
    profile: DimensionProfile,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn selected geometry into deterministic mechanical dimension intent."""

    selected = geometry_only(records)
    if not selected:
        raise ValueError("The selected target contains no supported geometry")
    bounds = _combined_bounds(record.bbox for record in selected)
    width = bounds.max_x - bounds.min_x
    height = bounds.max_y - bounds.min_y
    scale = max(width, height, 1.0)
    spacing = options.spacing or max(profile.row_spacing * profile.scale_factor, scale * 0.045)
    tolerance = max(scale * 1e-5, 1e-6)
    first_lane = spacing * 1.5
    second_lane = first_lane + spacing
    candidates: list[dict[str, Any]] = []

    def linear(
        p1: tuple[float, float],
        p2: tuple[float, float],
        base: tuple[float, float],
        *,
        category: str,
        text: str | None = None,
    ) -> None:
        candidates.append(
            {
                "kind": "linear",
                "geometry": {"p1": list(p1), "p2": list(p2)},
                "placement": {
                    "base": list(base),
                    "angle": 0 if abs(p1[1] - p2[1]) <= tolerance else 90,
                    "label_anchor": list(base),
                },
                "text": text,
                "metadata": {"category": category},
            }
        )

    if options.include_overall:
        linear(
            (bounds.min_x, bounds.min_y),
            (bounds.max_x, bounds.min_y),
            (bounds.min_x, bounds.min_y - first_lane),
            category="overall_width",
        )
        linear(
            (bounds.min_x, bounds.min_y),
            (bounds.min_x, bounds.max_y),
            (bounds.min_x - first_lane, bounds.min_y),
            category="overall_height",
        )

    points: list[tuple[float, float]] = []
    circles: list[dict[str, Any]] = []
    arcs: list[dict[str, Any]] = []
    for record in selected:
        detail = _record_detail(record)
        points.extend(detail.get("points", []))
        if detail.get("center"):
            points.append(detail["center"])
        if record.entity_type == "CIRCLE" and detail.get("center"):
            circles.append({"record": record, **detail})
        if record.entity_type == "ARC" and detail.get("center"):
            arcs.append({"record": record, **detail})

    if options.include_features and options.mode != "minimal":
        cap = 12 if options.mode == "balanced" else 24
        x_values = _thin(_unique([point[0] for point in points], tolerance), cap)
        y_values = _thin(_unique([point[1] for point in points], tolerance), cap)
        min_segment = spacing * 0.65
        layout = profile.preferred_layout
        if layout in {"baseline", "ordinate"}:
            for index, value in enumerate(x_values[1:-1]):
                if value - bounds.min_x >= min_segment:
                    linear(
                        (bounds.min_x, bounds.min_y),
                        (value, bounds.min_y),
                        (bounds.min_x, bounds.min_y - second_lane - spacing * index),
                        category=f"{layout}_x",
                    )
            for index, value in enumerate(y_values[1:-1]):
                if value - bounds.min_y >= min_segment:
                    linear(
                        (bounds.min_x, bounds.min_y),
                        (bounds.min_x, value),
                        (bounds.min_x - second_lane - spacing * index, bounds.min_y),
                        category=f"{layout}_y",
                    )
        else:
            for left, right in zip(x_values, x_values[1:]):
                if right - left >= min_segment:
                    linear(
                        (left, bounds.min_y),
                        (right, bounds.min_y),
                        (left, bounds.min_y - second_lane),
                        category="chain_x",
                    )
            for bottom, top in zip(y_values, y_values[1:]):
                if top - bottom >= min_segment:
                    linear(
                        (bounds.min_x, bottom),
                        (bounds.min_x, top),
                        (bounds.min_x - second_lane, bottom),
                        category="chain_y",
                    )

    intelligence_entities = _intelligence_entities(selected)
    feature_report = recognize_mechanical_features(intelligence_entities, tolerance=tolerance)
    repeated_holes = feature_report.by_kind("repeated_hole_pattern")
    grouped_hole_handles = {
        handle for feature in repeated_holes for handle in feature.entity_handles
    }
    circle_by_handle = {item["record"].handle: item for item in circles}
    feature_angles = (45.0, 135.0, 225.0, 315.0)

    if options.include_holes:
        for group_index, feature in enumerate(repeated_holes):
            representative = circle_by_handle.get(feature.entity_handles[0])
            if representative is None:
                continue
            count = int(feature.geometry["quantity"])
            angle = math.radians(feature_angles[group_index % 4])
            point = _leader_point(representative, spacing, angle, 1 + group_index // 4)
            candidates.append(
                {
                    "kind": "diameter",
                    "geometry": {
                        "entity_id": representative["record"].handle,
                        "center": list(representative["center"]),
                        "point": list(point),
                    },
                    "placement": {"label_anchor": list(point)},
                    "text": profile.quantity_format.format(
                        count=count,
                        value=f"{_autocad_text(profile.diameter_prefix)}<>",
                    ),
                    "metadata": {
                        "category": "repeated_holes",
                        "entity_ids": list(feature.entity_handles),
                        "notation": feature.notation,
                    },
                }
            )
        singles = [
            circle for circle in circles if circle["record"].handle not in grouped_hole_handles
        ]
        for index, circle in enumerate(singles):
            angle = math.radians(feature_angles[index % 4])
            point = _leader_point(circle, spacing, angle, 1 + index // 4)
            candidates.append(
                {
                    "kind": "diameter",
                    "geometry": {
                        "entity_id": circle["record"].handle,
                        "center": list(circle["center"]),
                        "point": list(point),
                    },
                    "placement": {"label_anchor": list(point)},
                    "text": f"{_autocad_text(profile.diameter_prefix)}<>",
                    "metadata": {"category": "hole_diameter"},
                }
            )

    if options.include_centers and profile.include_centerlines:
        for circle in circles:
            candidates.append(
                {
                    "kind": "center",
                    "geometry": {
                        "entity_id": circle["record"].handle,
                        "center": list(circle["center"]),
                        "size": min(max(circle["radius"] * 0.22, spacing * 0.12), spacing * 0.4),
                    },
                    "placement": {"label_anchor": list(circle["center"])},
                    "metadata": {"category": "center_mark"},
                }
            )

    repeated_fillet_handles: set[str] = set()
    if options.include_arcs:
        for group_index, feature in enumerate(feature_report.by_kind("repeated_fillet")):
            repeated_fillet_handles.update(feature.entity_handles)
            arc = next(
                (item for item in arcs if item["record"].handle == feature.entity_handles[0]),
                None,
            )
            if arc is None:
                continue
            angle = math.radians(feature_angles[(group_index + 1) % 4])
            point = _leader_point(arc, spacing, angle, 1 + group_index // 4)
            candidates.append(
                {
                    "kind": "radius",
                    "geometry": {
                        "entity_id": arc["record"].handle,
                        "center": list(arc["center"]),
                        "point": list(point),
                    },
                    "placement": {"label_anchor": list(point)},
                    "text": profile.quantity_format.format(
                        count=int(feature.geometry["quantity"]),
                        value=f"{_autocad_text(profile.radius_prefix)}<>",
                    ),
                    "metadata": {"category": "repeated_fillet", "notation": feature.notation},
                }
            )
        remaining_arcs = [
            arc for arc in arcs if arc["record"].handle not in repeated_fillet_handles
        ]
        for index, arc in enumerate(remaining_arcs):
            angle = math.radians(feature_angles[(index + 1) % 4])
            point = _leader_point(arc, spacing, angle, 1 + index // 4)
            candidates.append(
                {
                    "kind": "radius",
                    "geometry": {
                        "entity_id": arc["record"].handle,
                        "center": list(arc["center"]),
                        "point": list(point),
                    },
                    "placement": {"label_anchor": list(point)},
                    "text": f"{_autocad_text(profile.radius_prefix)}<>",
                    "metadata": {"category": "arc_radius"},
                }
            )

    if options.detect_symmetry:
        for feature in feature_report.by_kind("symmetric_hole_pattern"):
            for pair_index, pair in enumerate(feature.geometry.get("pairs", [])[:8]):
                left, right = tuple(pair[0]), tuple(pair[1])
                horizontal = abs(left[1] - right[1]) <= tolerance
                base = (
                    (left[0], bounds.max_y + first_lane + spacing * (pair_index + 1))
                    if horizontal
                    else (bounds.max_x + first_lane + spacing * (pair_index + 1), left[1])
                )
                linear(left, right, base, category="symmetric_center_distance")

    for feature in (*feature_report.by_kind("slot"), *feature_report.by_kind("chamfer")):
        geometry = feature.geometry
        if feature.kind == "slot" and len(geometry.get("centers", [])) == 2:
            left, right = tuple(geometry["centers"][0]), tuple(geometry["centers"][1])
            base = (left[0], bounds.max_y + second_lane)
            linear(left, right, base, category="slot_center_distance")
        anchor = _feature_anchor(feature.geometry, bounds, spacing)
        candidates.append(
            {
                "kind": "text",
                "geometry": {"point": list(anchor)},
                "placement": {"label_anchor": list(anchor)},
                "text": _autocad_text(feature.notation),
                "metadata": {"category": feature.kind, "feature_id": feature.feature_id},
            }
        )

    summary = {
        "spacing": spacing,
        "extents": bounds.to_dict(),
        "mechanical_features": feature_report.to_dict(),
    }
    return candidates, summary


async def commit_dimension_plan(
    backend: Any,
    plan: DimensionPlan,
    profile: DimensionProfile,
) -> dict[str, Any]:
    if backend.name == "ezdxf":
        return _commit_ezdxf(backend, plan, profile)
    if backend.name == "file_ipc":
        return await _commit_file_ipc(backend, plan, profile)
    raise RuntimeError(f"Dimension workflow is not supported by backend {backend.name!r}")


async def apply_file_ipc_repairs(
    backend: Any,
    actions: Iterable[dict[str, Any]],
    profile: DimensionProfile,
) -> dict[str, Any]:
    """Apply audited File IPC repair actions in one AutoCAD UNDO group."""

    lisp_path = (LISP_DIR / "auto_dimension.lsp").resolve()
    token = uuid.uuid4().hex[:12]
    ipc_dir = Path(backend._ipc_dir)  # noqa: SLF001
    actions_path = ipc_dir / f"autocad_mcp_dim_repairs_{token}.lspdata"
    report_path = ipc_dir / f"autocad_mcp_dim_repairs_{token}.json"
    supported: list[str] = []
    for action in actions:
        kind = str(action.get("action", ""))
        handle = _lisp_string(str(action.get("handle", "")))
        if kind == "delete":
            supported.append(f'("delete" {handle})')
        elif kind == "set_layer":
            supported.append(f'("set_layer" {handle} {_lisp_string(str(action["value"]))})')
        elif kind == "set_style":
            supported.append(f'("set_style" {handle} {_lisp_string(str(action["value"]))})')
        elif kind == "move_to_next_lane":
            delta = action["delta"]
            supported.append(
                f'("move" {handle} {float(delta[0]):.12g} {float(delta[1]):.12g})'
            )
    if not supported:
        return {"actions_applied": 0, "actions_failed": 0, "undo_group": "not_needed"}
    try:
        actions_path.write_text("\n".join(supported), encoding="ascii")
        result = await backend.annotation_repair_dimensions(
            lisp_path=str(lisp_path),
            actions_path=str(actions_path),
            report_path=str(report_path),
            dimension_layer=profile.layer,
            dimstyle=profile.dimstyle,
        )
        if not result.ok:
            raise RuntimeError(result.error or "AutoCAD dimension repair failed")
        if not report_path.exists():
            raise RuntimeError("AutoCAD did not produce a dimension repair report")
        report = _read_json_file(report_path)
        if report.get("ok") is False:
            raise RuntimeError(str(report.get("error", "AutoCAD dimension repair failed")))
        return report
    finally:
        for path in (actions_path, report_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def render_plan_preview(
    records: Iterable[EntityRecord],
    plan: DimensionPlan | None = None,
    *,
    parts: Iterable[Any] = (),
) -> str:
    """Return a PNG as base64; rendering is entirely in memory and side-effect free."""

    records = list(records)
    boxes = [record.bbox for record in records]
    if plan:
        for item in plan.dimensions:
            for point in _dimension_points(item):
                boxes.append(Bounds(point[0], point[1], point[0], point[1]))
    if not boxes:
        raise ValueError("Nothing is available to preview")
    bounds = _combined_bounds(boxes)
    margin = max(bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y, 1.0) * 0.08
    canvas = (1200, 800)
    image = Image.new("RGB", canvas, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    transform = _world_transform(bounds, canvas, margin)

    for record in records:
        detail = _record_detail(record)
        if record.entity_type in {"LINE", "LWPOLYLINE", "POLYLINE"}:
            points = [transform(point) for point in detail.get("points", [])]
            if len(points) >= 2:
                draw.line(points, fill=(35, 35, 35), width=3)
                if detail.get("closed"):
                    draw.line([points[-1], points[0]], fill=(35, 35, 35), width=3)
        elif record.entity_type in {"CIRCLE", "ARC"} and detail.get("center"):
            center = detail["center"]
            radius = detail["radius"]
            p1 = transform((center[0] - radius, center[1] - radius))
            p2 = transform((center[0] + radius, center[1] + radius))
            pixel_box = _pixel_box(p1, p2)
            if record.entity_type == "ARC":
                start = float(detail.get("start_angle", 0.0))
                end = float(detail.get("end_angle", 360.0))
                if end <= start:
                    end += 360.0
                draw.arc(pixel_box, start=start, end=end, fill=(35, 35, 35), width=3)
            else:
                draw.ellipse(pixel_box, outline=(35, 35, 35), width=3)
        else:
            draw.rectangle(
                _pixel_box(
                    transform((record.bbox.min_x, record.bbox.min_y)),
                    transform((record.bbox.max_x, record.bbox.max_y)),
                ),
                outline=(90, 90, 90),
                width=2,
            )

    for part in parts:
        box = part.bbox
        top_left = transform((box.min_x, box.max_y))
        bottom_right = transform((box.max_x, box.min_y))
        draw.rectangle(_pixel_box(top_left, bottom_right), outline=(70, 110, 255), width=4)
        draw.text((top_left[0] + 5, top_left[1] + 5), part.part_id, fill=(30, 70, 210), font=font)

    if plan:
        for item in plan.dimensions:
            points = _dimension_points(item)
            if len(points) >= 2:
                draw.line([transform(point) for point in points], fill=(0, 145, 175), width=2)
            anchor = item.preview_label.get("anchor") or (points[-1] if points else (0, 0))
            pixel = transform((float(anchor[0]), float(anchor[1])))
            draw.rectangle([pixel, (pixel[0] + 28, pixel[1] + 16)], fill=(215, 35, 45))
            draw.text((pixel[0] + 3, pixel[1] + 2), item.dimension_id, fill="white", font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def render_audit_preview(records: Iterable[EntityRecord], audit: Any) -> str:
    """Render green/yellow/red dimension QA boxes without touching AutoCAD."""

    records = list(records)
    boxes = [record.bbox for record in records]
    if not boxes:
        raise ValueError("Nothing is available to audit")
    bounds = _combined_bounds(boxes)
    margin = max(bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y, 1.0) * 0.08
    canvas = (1200, 800)
    image = Image.new("RGB", canvas, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    transform = _world_transform(bounds, canvas, margin)
    severity_by_handle: dict[str, str] = {}
    for issue in audit.issues:
        for handle in issue.dimension_handles:
            current = severity_by_handle.get(handle)
            if current != "error":
                severity_by_handle[handle] = issue.severity
    palette = {
        "ok": (25, 155, 75),
        "info": (25, 155, 75),
        "warning": (235, 175, 25),
        "error": (215, 45, 45),
    }
    for record in records:
        if record.entity_type == "DIMENSION":
            severity = severity_by_handle.get(record.handle, "ok")
            color = palette[severity]
            draw.rectangle(
                _pixel_box(
                    transform((record.bbox.min_x, record.bbox.max_y)),
                    transform((record.bbox.max_x, record.bbox.min_y)),
                ),
                outline=color,
                width=5,
            )
            draw.text(
                transform((record.bbox.min_x, record.bbox.max_y)),
                record.handle,
                fill=color,
                font=font,
            )
        else:
            draw.rectangle(
                _pixel_box(
                    transform((record.bbox.min_x, record.bbox.max_y)),
                    transform((record.bbox.max_x, record.bbox.min_y)),
                ),
                outline=(100, 100, 100),
                width=1,
            )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def records_for_intelligence(records: Iterable[EntityRecord]) -> list[Any]:
    """Flatten backend-neutral records for mechanical recognition and audit."""

    return _intelligence_entities(records)


def _records_from_ezdxf_with_dimensions(modelspace: Any) -> list[EntityRecord]:
    records = records_from_ezdxf(
        modelspace,
        supported_types=GEOMETRY_TYPES,
    )
    for entity in modelspace:
        if entity.dxftype() != "DIMENSION":
            continue
        points = []
        for field in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
            value = entity.dxf.get(field)
            if value is not None:
                points.append((float(value.x), float(value.y)))
        if not points:
            continue
        bounds = Bounds(
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        )
        records.append(
            EntityRecord(
                handle=str(entity.dxf.handle),
                entity_type="DIMENSION",
                layer=str(entity.dxf.get("layer", "0")),
                bbox=bounds,
                geometry=entity,
            )
        )
    return records


def _record_detail(record: EntityRecord) -> dict[str, Any]:
    entity = record.geometry
    if isinstance(entity, dict):
        detail = dict(entity)
        if "points" in detail:
            detail["points"] = [tuple(map(float, point[:2])) for point in detail["points"]]
        if "center" in detail:
            detail["center"] = tuple(map(float, detail["center"][:2]))
        return detail
    if entity is None:
        return {}
    entity_type = record.entity_type
    if entity_type == "LINE":
        return {
            "points": [
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            ]
        }
    if entity_type == "LWPOLYLINE":
        return {
            "points": [(float(x), float(y)) for x, y in entity.get_points("xy")],
            "closed": bool(entity.closed),
        }
    if entity_type == "POLYLINE":
        return {
            "points": [
                (float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                for vertex in entity.vertices
            ],
            "closed": bool(entity.is_closed),
        }
    if entity_type in {"CIRCLE", "ARC"}:
        result = {
            "center": (float(entity.dxf.center.x), float(entity.dxf.center.y)),
            "radius": float(entity.dxf.radius),
        }
        if entity_type == "ARC":
            result.update(
                start_angle=float(entity.dxf.start_angle),
                end_angle=float(entity.dxf.end_angle),
            )
        return result
    if entity_type == "DIMENSION":
        result: dict[str, Any] = {
            "dimtype": int(entity.dxf.get("dimtype", 0)),
            "angle": float(entity.dxf.get("angle", 0.0)),
            "dimstyle": str(entity.dxf.get("dimstyle", "")),
            "text": str(entity.dxf.get("text", "<>")),
        }
        try:
            result["measurement"] = float(entity.get_measurement())
        except (AttributeError, TypeError, ValueError, ZeroDivisionError):
            result["measurement"] = None
        for field in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
            value = entity.dxf.get(field)
            if value is not None:
                result[field] = (float(value.x), float(value.y))
        return result
    return {}


def _intelligence_entities(records: Iterable[EntityRecord]) -> list[Any]:
    result: list[Any] = []
    for record in records:
        if not isinstance(record.geometry, dict):
            if record.geometry is not None:
                result.append(record.geometry)
            continue
        result.append(
            {
                "type": record.entity_type,
                "handle": record.handle,
                "layer": record.layer,
                **record.geometry,
            }
        )
    return result


def _commit_ezdxf(backend: Any, plan: DimensionPlan, profile: DimensionProfile) -> dict[str, Any]:
    document = backend._doc  # noqa: SLF001
    modelspace = backend._msp  # noqa: SLF001
    if document is None or modelspace is None:
        raise RuntimeError("No document open")
    staging_layer = f"__MCP_DIM_STAGE_{uuid.uuid4().hex[:8]}"
    document.layers.add(staging_layer, color=2)
    staged_profile = replace(profile, layer=staging_layer)
    style_created = False
    if profile.dimstyle not in document.dimstyles:
        document.dimstyles.duplicate_entry("Standard", profile.dimstyle)
        style_created = True

    created = 0
    try:
        for item in plan.dimensions:
            try:
                entity = _create_ezdxf_dimension(modelspace, item, staged_profile)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not create planned dimension {item.dimension_id}: {exc}"
                ) from exc
            if entity is None:
                raise RuntimeError(
                    f"Unsupported planned dimension kind for {item.dimension_id}: {item.kind}"
                )
            created += 1

        if profile.layer not in document.layers:
            document.layers.add(profile.layer, color=2)
        if plan.target.get("clear_existing"):
            for entity in list(modelspace):
                if entity.dxf.get("layer", "0") == profile.layer:
                    modelspace.delete_entity(entity)
        for entity in list(modelspace):
            if entity.dxf.get("layer", "0") == staging_layer:
                entity.dxf.layer = profile.layer
                if entity.dxftype() == "DIMENSION":
                    entity.render()
        document.layers.remove(staging_layer)
        return {
            "backend": "ezdxf",
            "dimensions_created": created,
            "instructions_failed": 0,
            "failed_dimension_ids": [],
            "undo_group": "transactional_batch",
            "dimension_layer": profile.layer,
            "dimstyle_applied": profile.dimstyle,
        }
    except BaseException:
        for entity in list(modelspace):
            if entity.dxf.get("layer", "0") == staging_layer:
                modelspace.delete_entity(entity)
        if staging_layer in document.layers:
            document.layers.remove(staging_layer)
        if style_created and profile.dimstyle in document.dimstyles:
            document.dimstyles.remove(profile.dimstyle)
        raise


def _create_ezdxf_dimension(
    modelspace: Any,
    item: PlannedDimension,
    profile: DimensionProfile,
) -> Any:
    kind = item.kind
    geometry = item.geometry
    placement = item.placement
    override = {
        "dimtxt": profile.text_height,
        "dimasz": profile.arrow_size,
        "dimdec": profile.precision,
        "dimscale": profile.scale_factor,
        "dimtol": 0 if profile.tolerance_mode == "none" else 1,
        "dimtp": profile.tolerance_upper,
        "dimtm": profile.tolerance_lower,
    }
    if kind == "linear":
        dim = modelspace.add_linear_dim(
            base=tuple(placement["base"]),
            p1=tuple(geometry["p1"]),
            p2=tuple(geometry["p2"]),
            angle=float(placement.get("angle", 0)),
            dimstyle=profile.dimstyle,
            override=override,
            dxfattribs={"layer": profile.layer},
        )
        if item.text:
            dim.set_text(item.text)
        dim.render()
        return dim.dimension
    if kind in {"diameter", "radius"}:
        center = tuple(geometry["center"])
        point = tuple(geometry["point"])
        if kind == "diameter":
            dim = modelspace.add_diameter_dim(
                center=center,
                mpoint=point,
                dimstyle=profile.dimstyle,
                override=override,
                dxfattribs={"layer": profile.layer},
            )
        else:
            dim = modelspace.add_radius_dim(
                center=center,
                mpoint=point,
                dimstyle=profile.dimstyle,
                override=override,
                dxfattribs={"layer": profile.layer},
            )
        if item.text:
            dim.set_text(item.text)
        dim.render()
        return dim.dimension
    if kind == "center":
        center = tuple(geometry["center"])
        size = float(geometry.get("size", profile.text_height))
        modelspace.add_line(
            (center[0] - size, center[1]),
            (center[0] + size, center[1]),
            dxfattribs={"layer": profile.layer},
        )
        return modelspace.add_line(
            (center[0], center[1] - size),
            (center[0], center[1] + size),
            dxfattribs={"layer": profile.layer},
        )
    if kind == "text":
        return modelspace.add_text(
            item.text or "",
            dxfattribs={
                "insert": tuple(geometry["point"]),
                "height": profile.text_height,
                "layer": profile.layer,
            },
        )
    return None


async def _commit_file_ipc(
    backend: Any,
    plan: DimensionPlan,
    profile: DimensionProfile,
) -> dict[str, Any]:
    lisp_path = (LISP_DIR / "auto_dimension.lsp").resolve()
    token = uuid.uuid4().hex[:12]
    ipc_dir = Path(backend._ipc_dir)  # noqa: SLF001
    plan_path = ipc_dir / f"autocad_mcp_dim_plan_{token}.lspdata"
    report_path = ipc_dir / f"autocad_mcp_dim_commit_{token}.json"
    try:
        plan_path.write_text(
            "\n".join(_dimension_to_lisp_data(item) for item in plan.dimensions),
            encoding="ascii",
        )
        result = await backend.annotation_commit_dimension_plan(
            lisp_path=str(lisp_path),
            plan_path=str(plan_path),
            report_path=str(report_path),
            dimension_layer=profile.layer,
            dimstyle=profile.dimstyle,
            scale_factor=profile.scale_factor,
            clear_existing=bool(plan.target.get("clear_existing")),
            text_height=profile.text_height,
            arrow_size=profile.arrow_size,
            precision=profile.precision,
            tolerance_mode=profile.tolerance_mode,
            tolerance_upper=profile.tolerance_upper,
            tolerance_lower=profile.tolerance_lower,
        )
        if not result.ok:
            raise RuntimeError(result.error or "AutoCAD dimension plan commit failed")
        if not report_path.exists():
            raise RuntimeError("AutoCAD did not produce a dimension commit report")
        report = _read_json_file(report_path)
        if report.get("ok") is False:
            raise RuntimeError(str(report.get("error", "AutoCAD dimension plan commit failed")))
        return {"backend": "file_ipc", **report, "dimension_layer": profile.layer}
    finally:
        for path in (plan_path, report_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _dimension_to_lisp_data(item: PlannedDimension) -> str:
    kind = item.kind
    geometry = item.geometry
    placement = item.placement
    text = _lisp_string(item.text or "")
    if kind == "linear":
        return (
            f'("linear" {_lisp_point(geometry["p1"])} {_lisp_point(geometry["p2"])} '
            f'{_lisp_point(placement["base"])} {float(placement.get("angle", 0)):.12g} {text})'
        )
    if kind in {"diameter", "radius"}:
        return (
            f'("{kind}" {_lisp_string(str(geometry["entity_id"]))} '
            f'{_lisp_point(geometry["point"])} {text})'
        )
    if kind == "center":
        return f'("center" {_lisp_string(str(geometry["entity_id"]))})'
    if kind == "text":
        return f'("text" {_lisp_point(geometry["point"])} {text})'
    raise ValueError(f"Unsupported planned dimension kind: {kind}")


def _lisp_string(value: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise ValueError("Dimension text cannot contain control characters")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "File IPC dimension text must use ASCII/AutoCAD %% escape notation"
        ) from exc
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def _lisp_point(value: Iterable[float]) -> str:
    point = list(value)
    return f"({float(point[0]):.12g} {float(point[1]):.12g} 0.0)"


def _dimension_points(item: PlannedDimension) -> list[tuple[float, float]]:
    geometry = item.geometry
    placement = item.placement
    points: list[tuple[float, float]] = []
    for name in ("p1", "p2", "center", "point"):
        value = geometry.get(name)
        if value is not None:
            points.append((float(value[0]), float(value[1])))
    if placement.get("base") is not None:
        value = placement["base"]
        points.append((float(value[0]), float(value[1])))
    return points


def _combined_bounds(bounds: Iterable[Bounds]) -> Bounds:
    iterator = iter(bounds)
    result = next(iterator)
    for item in iterator:
        result = result.union(item)
    return result


def _unique(values: Iterable[float], tolerance: float) -> list[float]:
    result: list[float] = []
    for value in sorted(float(item) for item in values):
        if not result or abs(value - result[-1]) > tolerance:
            result.append(value)
    return result


def _thin(values: list[float], cap: int) -> list[float]:
    if len(values) <= cap:
        return values
    if cap <= 2:
        return [values[0], values[-1]]
    span = len(values) - 1
    return [values[round(index * span / (cap - 1))] for index in range(cap)]


def _leader_point(
    feature: dict[str, Any],
    spacing: float,
    angle: float,
    lane: int,
) -> tuple[float, float]:
    center = feature["center"]
    distance = float(feature["radius"]) + spacing * lane
    return (
        center[0] + distance * math.cos(angle),
        center[1] + distance * math.sin(angle),
    )


def _feature_anchor(
    geometry: dict[str, Any],
    bounds: Bounds,
    spacing: float,
) -> tuple[float, float]:
    if geometry.get("start") and geometry.get("end"):
        return (
            (geometry["start"][0] + geometry["end"][0]) / 2,
            (geometry["start"][1] + geometry["end"][1]) / 2 + spacing,
        )
    if geometry.get("centers"):
        centers = geometry["centers"]
        return (
            sum(point[0] for point in centers) / len(centers),
            max(point[1] for point in centers) + spacing,
        )
    return (bounds.max_x + spacing, bounds.max_y + spacing)


def _autocad_text(value: str) -> str:
    return value.replace("Ø", "%%c").replace("⌀", "%%c").replace("°", "%%d")


def _world_transform(bounds: Bounds, canvas: tuple[int, int], margin: float):
    min_x = bounds.min_x - margin
    min_y = bounds.min_y - margin
    max_x = bounds.max_x + margin
    max_y = bounds.max_y + margin
    scale = min(canvas[0] / max(max_x - min_x, 1e-9), canvas[1] / max(max_y - min_y, 1e-9))
    offset_x = (canvas[0] - (max_x - min_x) * scale) / 2
    offset_y = (canvas[1] - (max_y - min_y) * scale) / 2

    def transform(point: tuple[float, float]) -> tuple[int, int]:
        return (
            round(offset_x + (point[0] - min_x) * scale),
            round(canvas[1] - (offset_y + (point[1] - min_y) * scale)),
        )

    return transform


def _pixel_box(
    first: tuple[int, int],
    second: tuple[int, int],
) -> tuple[int, int, int, int]:
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[0], second[0]),
        max(first[1], second[1]),
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="cp1252"))
