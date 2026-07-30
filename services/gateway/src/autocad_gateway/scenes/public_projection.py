"""Strict public projection for pure scene-engine artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from autocad_contracts import (
    SceneContour,
    SceneCounts,
    SceneEvidence,
    SceneFeature,
    SceneIssue,
    SceneNode,
    SceneRelation,
    SceneResourceUris,
    SceneRoot,
    ToleranceProfile,
    canonical_scene_contour_id,
    canonical_scene_digest,
    canonical_scene_evidence_id,
    canonical_scene_feature_id,
    canonical_scene_issue_id,
    canonical_scene_source_digest,
)


PROJECTION_VERSION = "cad.entity-projection/2"


def _point(value: Any) -> dict[str, float]:
    return {"x": float(value[0]), "y": float(value[1])}


def _bounds(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "minimum": {"x": float(value.min_x), "y": float(value.min_y)},
        "maximum": {"x": float(value.max_x), "y": float(value.max_y)},
    }


def _geometry(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    name = type(value).__name__
    if name == "LineGeometry":
        return {"kind": "line", "start": _point(value.start), "end": _point(value.end)}
    if name == "CircleGeometry":
        return {
            "kind": "circle",
            "center": _point(value.center),
            "radius": float(value.radius),
        }
    if name == "ArcGeometry":
        return {
            "kind": "arc",
            "center": _point(value.center),
            "radius": float(value.radius),
            "start_angle_radians": float(value.start_angle_radians),
            "end_angle_radians": float(value.end_angle_radians),
        }
    if name == "PolylineGeometry":
        return {
            "kind": "polyline",
            "vertices": [_point(vertex.xy) for vertex in value.vertices],
            "bulges": [float(vertex.bulge) for vertex in value.vertices],
            "closed": bool(value.closed),
            "elevation": float(value.elevation),
        }
    raise ValueError("unsupported projected geometry")


def _digest(value: Any) -> str:
    body = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _entity_ids(node_ids: tuple[str, ...] | list[str], by_node: dict[str, Any]) -> list[str]:
    return sorted(by_node[node_id].source_entity_id for node_id in node_ids)


def project_artifact(
    artifact: Any,
    *,
    scene_id: str,
    source_snapshot_available: bool,
    include_sections: list[str],
    mechanical_features_enabled: bool,
) -> tuple[SceneRoot, dict[str, list[dict[str, Any]]], dict[str, str]]:
    by_node = {item.node_id: item for item in artifact.nodes}
    evidence: dict[str, SceneEvidence] = {}

    nodes: list[SceneNode] = []
    for item in artifact.nodes:
        evidence_id = canonical_scene_evidence_id(
            evidence_type="source_geometry",
            source_entity_ids=[item.source_entity_id],
            algorithm_version=PROJECTION_VERSION,
        )
        evidence[evidence_id] = SceneEvidence(
            evidence_id=evidence_id,
            evidence_type="source_geometry",
            evidence_strength=(
                "exact_source_geometry"
                if item.geometry_status == "exact"
                else "unsupported"
            ),
            source_node_ids=[item.node_id],
            source_entity_ids=[item.source_entity_id],
            algorithm_version=PROJECTION_VERSION,
            limitations=(
                [item.geometry_reason]
                if item.geometry_reason
                and item.geometry_reason
                in {
                    "source_geometry_truncated",
                    "entity_type_unsupported",
                    "source_geometry_unavailable",
                }
                else []
            ),
        )
        fingerprint = (
            item.fingerprint
            if isinstance(item.fingerprint, str)
            and item.fingerprint.startswith("sha256:")
            and len(item.fingerprint) == 71
            else canonical_scene_source_digest(
                {
                    "entity_id": item.source_entity_id,
                    "entity_type": item.entity_type,
                    "geometry": _geometry(item.geometry),
                }
            )
        )
        nodes.append(
            SceneNode(
                node_id=item.node_id,
                source_entity_id=item.source_entity_id,
                entity_type=item.entity_type,
                layer="layer_" + hashlib.sha256(
                    (item.layer or "0").encode("utf-8")
                ).hexdigest()[:16],
                space=item.space,
                bounds=_bounds(item.bounds),
                geometry=_geometry(item.geometry),
                geometry_status=item.geometry_status,
                fingerprint=fingerprint,
                source_runtime=item.source_runtime.lower().replace("-", "_"),
                source_capabilities=list(item.source_capabilities),
            )
        )

    relations: list[SceneRelation] = []
    for item in artifact.relations:
        entity_ids = _entity_ids(item.source_node_ids, by_node)
        evidence_id = canonical_scene_evidence_id(
            evidence_type=item.relation_type,
            source_entity_ids=entity_ids,
            algorithm_version=item.algorithm_version,
        )
        evidence[evidence_id] = SceneEvidence(
            evidence_id=evidence_id,
            evidence_type=item.relation_type,
            evidence_strength=item.evidence_strength,
            source_node_ids=list(item.source_node_ids),
            source_entity_ids=entity_ids,
            metrics=dict(item.metrics),
            algorithm_version=item.algorithm_version,
        )
        relations.append(
            SceneRelation(
                relation_id=item.relation_id,
                relation_type=item.relation_type,
                source_node_ids=list(item.source_node_ids),
                directionality=item.directionality,
                evidence_strength=item.evidence_strength,
                confidence=item.confidence,
                metrics=dict(item.metrics),
                tolerance_used=item.tolerance_used,
                algorithm_version=item.algorithm_version,
                evidence_ids=[evidence_id],
            )
        )

    contours: list[SceneContour] = []
    for item in artifact.contours:
        entity_ids = _entity_ids(item.source_node_ids, by_node)
        evidence_id = canonical_scene_evidence_id(
            evidence_type="contour",
            source_entity_ids=entity_ids,
            algorithm_version=item.algorithm_version,
        )
        evidence[evidence_id] = SceneEvidence(
            evidence_id=evidence_id,
            evidence_type="contour",
            evidence_strength="derived_exact",
            source_node_ids=list(item.source_node_ids),
            source_entity_ids=entity_ids,
            algorithm_version=item.algorithm_version,
        )
        contours.append(
            SceneContour(
                contour_id=canonical_scene_contour_id(
                    source_node_ids=list(item.source_node_ids),
                    algorithm_version=item.algorithm_version,
                ),
                source_node_ids=list(item.source_node_ids),
                closed=item.closed,
                bounds=_bounds(item.bounds),
                signed_area=None,
                orientation="undefined",
                algorithm_version=item.algorithm_version,
                evidence_ids=[evidence_id],
            )
        )

    features: list[SceneFeature] = []
    if mechanical_features_enabled:
        for item in artifact.features:
            entity_ids = _entity_ids(item.source_node_ids, by_node)
            evidence_id = canonical_scene_evidence_id(
                evidence_type=item.feature_type,
                source_entity_ids=entity_ids,
                algorithm_version=item.algorithm_version,
            )
            evidence[evidence_id] = SceneEvidence(
                evidence_id=evidence_id,
                evidence_type=item.feature_type,
                evidence_strength=item.evidence_strength,
                source_node_ids=list(item.source_node_ids),
                source_entity_ids=entity_ids,
                metrics={
                    key: float(value)
                    for key, value in item.geometry_summary
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                },
                algorithm_version=item.algorithm_version,
                limitations=list(item.limitations),
            )
            features.append(
                SceneFeature(
                    feature_id=canonical_scene_feature_id(
                        feature_type=item.feature_type,
                        source_evidence_ids=[evidence_id],
                        algorithm_version=item.algorithm_version,
                    ),
                    feature_type=item.feature_type,
                    source_node_ids=list(item.source_node_ids),
                    source_relation_ids=list(item.source_relation_ids),
                    confidence=item.confidence,
                    evidence_ids=[evidence_id],
                    algorithm_version=item.algorithm_version,
                    limitations=list(item.limitations),
                )
            )

    issues: list[SceneIssue] = []
    for item in artifact.issues:
        entity_ids = _entity_ids(item.source_node_ids, by_node)
        evidence_id = canonical_scene_evidence_id(
            evidence_type=item.code,
            source_entity_ids=entity_ids,
            algorithm_version=item.detector_version,
        )
        evidence[evidence_id] = SceneEvidence(
            evidence_id=evidence_id,
            evidence_type=item.code,
            evidence_strength="bounded_heuristic",
            source_node_ids=list(item.source_node_ids),
            source_entity_ids=entity_ids,
            algorithm_version=item.detector_version,
            limitations=["read_only_report"],
        )
        issues.append(
            SceneIssue(
                issue_id=canonical_scene_issue_id(
                    issue_code=item.code,
                    source_evidence_ids=[evidence_id],
                    detector_version=item.detector_version,
                ),
                code=item.code,
                severity=item.severity,
                source_node_ids=list(item.source_node_ids),
                source_relation_ids=list(item.source_relation_ids),
                message_key=item.message_key,
                evidence_ids=[evidence_id],
                confidence=item.confidence,
                suggested_action=item.suggested_action or None,
                write_authority=False,
            )
        )

    all_sections: dict[str, list[dict[str, Any]]] = {
        "nodes": [item.model_dump(mode="json") for item in nodes],
        "relations": [item.model_dump(mode="json") for item in relations],
        "contours": [item.model_dump(mode="json") for item in contours],
        "features": [item.model_dump(mode="json") for item in features],
        "issues": [item.model_dump(mode="json") for item in issues],
        "evidence": [
            item.model_dump(mode="json")
            for item in sorted(evidence.values(), key=lambda value: value.evidence_id)
        ],
    }
    sections = {name: all_sections[name] for name in include_sections}
    counts = SceneCounts(
        nodes=len(nodes),
        relations=len(relations),
        contours=len(contours),
        features=len(features),
        issues=len(issues),
        evidence=len(evidence),
    )
    tolerance = ToleranceProfile(
        drawing_unit=artifact.tolerance_profile.drawing_units,
        absolute_floor=artifact.tolerance_profile.absolute_floor,
        relative_to_extents=artifact.tolerance_profile.relative_component,
        angular_radians=artifact.tolerance_profile.angular,
        endpoint=artifact.tolerance_profile.endpoint,
        radius=artifact.tolerance_profile.radius,
        duplicate=artifact.tolerance_profile.duplicate,
        maximum_cap=artifact.tolerance_profile.maximum,
    )
    incomplete = [item for item in nodes if item.geometry_status != "exact"]
    complete = bool(artifact.complete) and not incomplete
    truncation_reasons = [] if complete else ["source_geometry_incomplete"]
    capabilities = sorted(
        {
            *artifact.context.source_capabilities,
            "scene.core/1",
            "scene.relations.core2d/1",
            "scene.contours.simple2d/1",
            "scene.issues.cleanup-audit/1",
            *(
                {"scene.features.mechanical2d/1"}
                if mechanical_features_enabled and complete
                else set()
            ),
        }
    )
    semantic = {
        "source_digest": artifact.source_digest,
        "projection_version": PROJECTION_VERSION,
        "engine_version": artifact.engine_version,
        "profile_id": artifact.context.profile_id,
        "tolerance_profile": tolerance.model_dump(mode="json"),
        "sections": sections,
    }
    scene_digest = canonical_scene_digest(semantic)
    uris = SceneResourceUris(
        **{
            name: f"cad://scenes/{scene_id}/{name}"
            for name in (
                "summary",
                "nodes",
                "relations",
                "contours",
                "features",
                "issues",
                "evidence",
            )
        }
    )
    root = SceneRoot(
        scene_id=scene_id,
        source_snapshot_id=artifact.context.source_snapshot_id,
        device_id=artifact.context.device_id,
        document_id=artifact.context.document_id,
        document_revision=artifact.context.document_revision,
        space="model",
        projection_version=PROJECTION_VERSION,
        engine_version=artifact.engine_version,
        profile_id=artifact.context.profile_id,
        tolerance_profile=tolerance,
        source_digest=artifact.source_digest,
        scene_digest=scene_digest,
        complete=complete,
        truncation_reasons=truncation_reasons,
        counts=counts,
        capabilities=capabilities,
        warnings=truncation_reasons,
        source_snapshot_available=source_snapshot_available,
        resource_uris=uris,
    )
    return root, sections, {name: _digest(items) for name, items in sections.items()}
