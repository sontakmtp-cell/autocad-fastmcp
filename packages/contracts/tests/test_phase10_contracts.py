from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    Bounds2d,
    CadBuildSceneInput,
    CadQuerySceneInput,
    CadQuerySceneOutput,
    CircleGeometry,
    Point2d,
    SceneCounts,
    SceneEvidence,
    SceneIssue,
    SceneNode,
    SceneResourceUris,
    SceneRoot,
    ToleranceProfile,
    canonical_scene_contour_id,
    canonical_scene_digest,
    canonical_scene_evidence_id,
    canonical_scene_feature_id,
    canonical_scene_issue_id,
    canonical_scene_node_id,
    canonical_scene_relation_id,
    canonical_scene_relation_evidence_id,
    canonical_scene_source_digest,
    phase10_build_scene_input_json_schema,
    phase10_query_scene_input_json_schema,
    phase10_query_scene_output_json_schema,
    phase10_scene_json_schema,
)


def _digest(character: str = "a") -> str:
    return "sha256:" + character * 64


def _node(entity_id: str = "entity-a") -> SceneNode:
    return SceneNode(
        node_id=canonical_scene_node_id(entity_id),
        source_entity_id=entity_id,
        entity_type="CIRCLE",
        layer="0",
        space="model",
        bounds=Bounds2d(
            minimum=Point2d(x=0.0, y=0.0),
            maximum=Point2d(x=2.0, y=2.0),
        ),
        geometry=CircleGeometry(center=Point2d(x=1.0, y=1.0), radius=1.0),
        geometry_status="exact",
        fingerprint=_digest(),
        source_runtime="managed-r25",
        source_capabilities=["entity.geometry.circle/1"],
    )


def _tolerance() -> ToleranceProfile:
    return ToleranceProfile(
        drawing_unit="mm",
        absolute_floor=0.001,
        relative_to_extents=0.000001,
        angular_radians=0.000001,
        endpoint=0.01,
        radius=0.01,
        duplicate=0.001,
        maximum_cap=1.0,
    )


def _resources(scene_id: str) -> SceneResourceUris:
    root = f"cad://scenes/{scene_id}"
    return SceneResourceUris(
        summary=f"{root}/summary",
        nodes=f"{root}/nodes",
        relations=f"{root}/relations",
        contours=f"{root}/contours",
        features=f"{root}/features",
        issues=f"{root}/issues",
        evidence=f"{root}/evidence",
    )


def _root(*, complete: bool = True) -> SceneRoot:
    scene_id = "scn_0123456789abcdef"
    return SceneRoot(
        scene_id=scene_id,
        source_snapshot_id="snapshot-a",
        device_id="device-a",
        document_id="document-a",
        document_revision="7419413270066305",
        space="model",
        projection_version="cad.entity-projection/2",
        engine_version="scene-engine/1.0.0",
        profile_id="mechanical-2d/1",
        tolerance_profile=_tolerance(),
        source_digest=_digest("b"),
        scene_digest=_digest("c"),
        complete=complete,
        truncation_reasons=[] if complete else ["scene-budget-exceeded"],
        counts=SceneCounts(
            nodes=1,
            relations=0,
            contours=0,
            features=0,
            issues=0,
            evidence=1,
            omitted=0 if complete else 1,
        ),
        capabilities=["scene.core/1"],
        warnings=[],
        source_snapshot_available=True,
        resource_uris=_resources(scene_id),
    )


def test_models_are_strict_frozen_extra_forbid_and_finite():
    node = _node()
    with pytest.raises(ValidationError):
        SceneNode.model_validate({**node.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        node.layer = "changed"
    with pytest.raises(ValidationError):
        Point2d(x=float("nan"), y=0.0)
    with pytest.raises(ValidationError):
        Point2d(x=float("inf"), y=0.0)
    with pytest.raises(ValidationError, match="requires geometry"):
        SceneNode.model_validate(
            {**node.model_dump(), "geometry": None, "geometry_status": "exact"}
        )
    with pytest.raises(ValidationError, match="forbids geometry"):
        SceneNode.model_validate(
            {**node.model_dump(), "geometry_status": "unsupported"}
        )


def test_confidence_tolerance_and_completeness_are_bounded():
    evidence_id = canonical_scene_evidence_id(
        evidence_type="circle-radius",
        source_entity_ids=["entity-a"],
        algorithm_version="relations/1.0.0",
    )
    with pytest.raises(ValidationError):
        SceneEvidence(
            evidence_id=evidence_id,
            evidence_type="circle-radius",
            evidence_strength="exact_source_geometry",
            source_node_ids=[canonical_scene_node_id("entity-a")],
            source_entity_ids=["entity-a"],
            metrics={"radius": float("inf")},
            algorithm_version="relations/1.0.0",
        )
    with pytest.raises(ValidationError, match="exceeds maximum cap"):
        ToleranceProfile.model_validate(
            {**_tolerance().model_dump(), "endpoint": 2.0}
        )
    with pytest.raises(ValidationError, match="cannot report truncation"):
        SceneRoot.model_validate(
            {**_root().model_dump(), "truncation_reasons": ["unexpected"]}
        )
    assert _root(complete=False).complete is False


@pytest.mark.parametrize("code", ["invalid_geometry", "self_intersection"])
def test_phase10_geometry_issue_codes_are_strict(code):
    evidence_id = canonical_scene_evidence_id(
        evidence_type="geometry-validation",
        source_entity_ids=["entity-a"],
        algorithm_version="issues/1.0.0",
    )
    issue = SceneIssue(
        issue_id=canonical_scene_issue_id(
            issue_code=code,
            source_evidence_ids=[evidence_id],
            detector_version="issues/1.0.0",
        ),
        code=code,
        severity="error",
        source_node_ids=[canonical_scene_node_id("entity-a")],
        message_key=code,
        evidence_ids=[evidence_id],
        confidence=1.0,
    )
    assert issue.code == code


def test_stable_ids_and_digests_are_canonical_and_domain_separated():
    node_a = canonical_scene_node_id("entity-a")
    node_b = canonical_scene_node_id("entity-b")
    forward = canonical_scene_relation_id(
        relation_type="parallel",
        directionality="symmetric",
        source_node_ids=[node_a, node_b],
        metrics={"angle_delta": 0.0},
    )
    reversed_nodes = canonical_scene_relation_id(
        relation_type="parallel",
        directionality="symmetric",
        source_node_ids=[node_b, node_a],
        metrics={"angle_delta": 0.0},
    )
    assert forward == reversed_nodes
    assert canonical_scene_relation_id(
        relation_type="inside",
        directionality="directed",
        source_node_ids=[node_a, node_b],
    ) != canonical_scene_relation_id(
        relation_type="inside",
        directionality="directed",
        source_node_ids=[node_b, node_a],
    )
    assert canonical_scene_source_digest({"a": 1, "b": 2}) == canonical_scene_source_digest(
        {"b": 2, "a": 1}
    )
    assert canonical_scene_source_digest({"a": 1}) != canonical_scene_digest({"a": 1})
    assert canonical_scene_digest({"nodes": [{"node_id": node_a}, {"node_id": node_b}]}) == (
        canonical_scene_digest({"nodes": [{"node_id": node_b}, {"node_id": node_a}]})
    )
    with pytest.raises(ValueError, match="finite"):
        canonical_scene_digest({"bad": float("nan")})
    with pytest.raises(ValueError, match="limit"):
        canonical_scene_digest({"too_large": "x" * 262_144})


def test_scene_digest_excludes_opaque_identity_and_runtime_links():
    first = _root().model_dump()
    second = {
        **first,
        "scene_id": "scn_fedcba9876543210",
        "scene_digest": _digest("d"),
        "source_snapshot_available": False,
        "resource_uris": _resources("scn_fedcba9876543210").model_dump(),
    }
    assert canonical_scene_digest(first) == canonical_scene_digest(second)
    source = {"snapshot_id": "snapshot-a", "source_digest": _digest("a")}
    assert canonical_scene_source_digest(source) == canonical_scene_source_digest(
        {**source, "source_digest": _digest("b")}
    )


def test_relation_evidence_identity_binds_metrics_direction_and_tolerance():
    common = {
        "relation_id": "rel_" + "a" * 64,
        "directionality": "symmetric",
        "metrics": {"offset": 0.0},
        "tolerance_used": 0.01,
        "algorithm_version": "scene-relations/2",
    }
    baseline = canonical_scene_relation_evidence_id(**common)

    assert baseline == canonical_scene_relation_evidence_id(
        **{**common, "metrics": {"offset": 0.0}}
    )
    assert baseline != canonical_scene_relation_evidence_id(
        **{**common, "metrics": {"offset": 0.001}}
    )
    assert baseline != canonical_scene_relation_evidence_id(
        **{**common, "directionality": "directed"}
    )
    assert baseline != canonical_scene_relation_evidence_id(
        **{**common, "tolerance_used": 0.02}
    )


def test_build_and_query_contracts_are_closed_and_canonical():
    build = CadBuildSceneInput(
        source_snapshot_id="snapshot-a",
        include_sections=["features", "nodes"],
        idempotency_key="request:1",
    )
    assert build.include_sections == ["features", "nodes"]
    with pytest.raises(ValidationError, match="duplicates"):
        CadBuildSceneInput(
            source_snapshot_id="snapshot-a",
            include_sections=["nodes", "nodes"],
            idempotency_key="request:1",
        )
    query = CadQuerySceneInput(
        scene_id="scn_0123456789abcdef",
        section="relations",
        relation_types=["parallel", "connected_endpoint"],
        cursor="payload.signature",
    )
    assert query.relation_types == ["connected_endpoint", "parallel"]
    with pytest.raises(ValidationError, match="does not apply"):
        CadQuerySceneInput(
            scene_id="scn_0123456789abcdef",
            section="nodes",
            relation_types=["parallel"],
        )
    with pytest.raises(ValidationError):
        CadQuerySceneInput(
            scene_id="scn_0123456789abcdef",
            section="nodes",
            limit=201,
        )


def test_query_output_rejects_items_from_another_section():
    with pytest.raises(ValidationError, match="do not match"):
        CadQuerySceneOutput(
            correlation_id="correlation-a",
            scene_id="scn_0123456789abcdef",
            scene_digest=_digest(),
            section="relations",
            items=[_node()],
            total=1,
            resource_uri="cad://scenes/scn_0123456789abcdef/relations",
        )


def test_phase10_golden_vector_matches_checked_in_fixture():
    node_a = canonical_scene_node_id("entity-a")
    node_b = canonical_scene_node_id("entity-b")
    evidence = canonical_scene_evidence_id(
        evidence_type="line-endpoints",
        source_entity_ids=["entity-b", "entity-a"],
        algorithm_version="relations/1.0.0",
    )
    generated = {
        "contour": canonical_scene_contour_id(
            source_node_ids=[node_b, node_a],
            algorithm_version="contours/1.0.0",
        ),
        "directed_relation": canonical_scene_relation_id(
            relation_type="inside",
            directionality="directed",
            source_node_ids=[node_a, node_b],
        ),
        "evidence": evidence,
        "feature": canonical_scene_feature_id(
            feature_type="hole",
            source_evidence_ids=[evidence],
            algorithm_version="holes/1.0.0",
        ),
        "issue": canonical_scene_issue_id(
            issue_code="open_contour",
            source_evidence_ids=[evidence],
            detector_version="issues/1.0.0",
        ),
        "node_a": node_a,
        "node_b": node_b,
        "scene_digest": canonical_scene_digest(
            {"nodes": [node_b, node_a], "relations": []}
        ),
        "source_digest": canonical_scene_source_digest(
            {
                "snapshot_id": "snapshot-a",
                "projections": [
                    {
                        "entity_id": "entity-a",
                        "fingerprint": _digest(),
                    }
                ],
            }
        ),
        "symmetric_relation": canonical_scene_relation_id(
            relation_type="parallel",
            directionality="symmetric",
            source_node_ids=[node_b, node_a],
            metrics={"angle_delta": 0.0},
        ),
    }
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "phase10-scene-digest-vector.json"
        ).read_text(encoding="utf-8")
    )
    assert fixture == {
        "fixture_version": "cad.phase10.scene-digest-vector/1",
        "expected": generated,
    }


def test_runtime_schemas_keep_strict_object_boundaries():
    schemas = [
        phase10_scene_json_schema(),
        phase10_build_scene_input_json_schema(),
        phase10_query_scene_input_json_schema(),
        phase10_query_scene_output_json_schema(),
    ]
    assert all(schema["additionalProperties"] is False for schema in schemas)
