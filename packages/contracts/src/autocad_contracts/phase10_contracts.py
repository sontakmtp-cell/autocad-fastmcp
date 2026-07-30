"""Strict, data-only contracts for Phase 10 drawing scenes."""

from __future__ import annotations

import math
import re
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .agent_protocol import canonical_json

CAD_SCENE_SCHEMA_VERSION = "cad.scene/1"
CAD_SCENE_NODE_SCHEMA_VERSION = "cad.scene-node/1"
CAD_SCENE_RELATION_SCHEMA_VERSION = "cad.scene-relation/1"
CAD_SCENE_CONTOUR_SCHEMA_VERSION = "cad.scene-contour/1"
CAD_SCENE_FEATURE_SCHEMA_VERSION = "cad.scene-feature/1"
CAD_SCENE_ISSUE_SCHEMA_VERSION = "cad.scene-issue/1"
CAD_SCENE_EVIDENCE_SCHEMA_VERSION = "cad.scene-evidence/1"
CAD_SCENE_QUERY_SCHEMA_VERSION = "cad.scene-query/1"
CAD_SCENE_PUBLIC_CONTRACT_VERSION = "cad.mcp/1.6"

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_CODE_PATTERN = r"^[a-z][a-z0-9._-]{0,63}$"
_ENTITY_TYPE_PATTERN = r"^[A-Z][A-Z0-9_]{0,63}$"
_CURSOR_PATTERN = r"^[A-Za-z0-9_.-]{1,512}$"
_MAX_MODEL_BYTES = 262_144
_MAX_JSON_DEPTH = 16
_MAX_CONTAINER_ITEMS = 4_096

PublicId = Annotated[str, StringConstraints(pattern=_ID_PATTERN)]
NodeId = Annotated[str, StringConstraints(pattern=r"^nod_[0-9a-f]{64}$")]
RelationId = Annotated[str, StringConstraints(pattern=r"^rel_[0-9a-f]{64}$")]
ContourId = Annotated[str, StringConstraints(pattern=r"^ctr_[0-9a-f]{64}$")]
FeatureId = Annotated[str, StringConstraints(pattern=r"^fea_[0-9a-f]{64}$")]
IssueId = Annotated[str, StringConstraints(pattern=r"^iss_[0-9a-f]{64}$")]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^evd_[0-9a-f]{64}$")]
Digest = Annotated[str, StringConstraints(pattern=_DIGEST_PATTERN)]
Version = Annotated[str, StringConstraints(pattern=_VERSION_PATTERN)]
Code = Annotated[str, StringConstraints(pattern=_CODE_PATTERN)]
Capability = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,62}/[1-9][0-9]*$")
]
EntityType = Annotated[str, StringConstraints(pattern=_ENTITY_TYPE_PATTERN)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]

SceneSection = Literal[
    "nodes",
    "relations",
    "contours",
    "features",
    "issues",
    "evidence",
]
GeometryStatus = Literal[
    "exact",
    "bounded_projection",
    "truncated",
    "unsupported",
    "unavailable",
    "invalid",
]
EvidenceStrength = Literal[
    "exact_source_geometry",
    "derived_exact",
    "bounded_heuristic",
    "unsupported",
]
RelationType = Literal[
    "connected_endpoint",
    "touch",
    "intersect",
    "overlap",
    "duplicate_geometry",
    "inside",
    "contains",
    "parallel",
    "perpendicular",
    "concentric",
    "aligned",
]
FeatureType = Literal[
    "part",
    "hole",
    "repeated_hole_pattern",
    "concentric_group",
    "slot",
    "centerline_candidate",
    "annotation_link",
]
IssueCode = Literal[
    "duplicate_geometry",
    "degenerate_geometry",
    "open_contour",
    "unsupported_geometry",
    "truncated_geometry",
    "orphan_annotation",
    "ambiguous_annotation",
    "inconsistent_repeated_feature",
]


def _validate_bounded_json(value: Any, *, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("scene contract nesting exceeds limit")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("scene contract numbers must be finite")
    if isinstance(value, dict):
        if len(value) > _MAX_CONTAINER_ITEMS or not all(isinstance(key, str) for key in value):
            raise ValueError("scene contract object is invalid")
        for item in value.values():
            _validate_bounded_json(item, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise ValueError("scene contract array exceeds limit")
        for item in value:
            _validate_bounded_json(item, depth=depth + 1)


class Phase10Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="after")
    def _bounded_and_finite(self) -> "Phase10Model":
        payload = self.model_dump(mode="json")
        _validate_bounded_json(payload)
        if len(canonical_json(payload).encode("utf-8")) > _MAX_MODEL_BYTES:
            raise ValueError("scene contract exceeds byte limit")
        return self


class Point2d(Phase10Model):
    x: FiniteFloat
    y: FiniteFloat


class Bounds2d(Phase10Model):
    minimum: Point2d
    maximum: Point2d

    @model_validator(mode="after")
    def _ordered(self) -> "Bounds2d":
        if self.minimum.x > self.maximum.x or self.minimum.y > self.maximum.y:
            raise ValueError("bounds minimum must not exceed maximum")
        return self


class LineGeometry(Phase10Model):
    kind: Literal["line"] = "line"
    start: Point2d
    end: Point2d


class CircleGeometry(Phase10Model):
    kind: Literal["circle"] = "circle"
    center: Point2d
    radius: PositiveFiniteFloat


class ArcGeometry(Phase10Model):
    kind: Literal["arc"] = "arc"
    center: Point2d
    radius: PositiveFiniteFloat
    start_angle_radians: FiniteFloat
    end_angle_radians: FiniteFloat


class PolylineGeometry(Phase10Model):
    kind: Literal["polyline"] = "polyline"
    vertices: list[Point2d] = Field(min_length=2, max_length=4_096)
    bulges: list[FiniteFloat] = Field(default_factory=list, max_length=4_096)
    closed: bool
    elevation: FiniteFloat = 0.0

    @model_validator(mode="after")
    def _bulges_match_vertices(self) -> "PolylineGeometry":
        if self.bulges and len(self.bulges) != len(self.vertices):
            raise ValueError("polyline bulges must match vertices")
        return self


SceneGeometry = Annotated[
    LineGeometry | CircleGeometry | ArcGeometry | PolylineGeometry,
    Field(discriminator="kind"),
]


class ToleranceProfile(Phase10Model):
    profile_id: Literal["mechanical-2d/1"] = "mechanical-2d/1"
    drawing_unit: str = Field(min_length=1, max_length=32)
    absolute_floor: PositiveFiniteFloat
    relative_to_extents: PositiveFiniteFloat
    angular_radians: PositiveFiniteFloat
    endpoint: PositiveFiniteFloat
    radius: PositiveFiniteFloat
    duplicate: PositiveFiniteFloat
    maximum_cap: PositiveFiniteFloat

    @model_validator(mode="after")
    def _within_cap(self) -> "ToleranceProfile":
        if any(
            value > self.maximum_cap
            for value in (
                self.absolute_floor,
                self.endpoint,
                self.radius,
                self.duplicate,
            )
        ):
            raise ValueError("linear tolerance exceeds maximum cap")
        return self


class SceneEvidence(Phase10Model):
    schema_version: Literal["cad.scene-evidence/1"] = CAD_SCENE_EVIDENCE_SCHEMA_VERSION
    evidence_id: EvidenceId
    evidence_type: Code
    evidence_strength: EvidenceStrength
    source_node_ids: list[NodeId] = Field(default_factory=list, max_length=64)
    source_entity_ids: list[PublicId] = Field(default_factory=list, max_length=64)
    metrics: dict[Code, FiniteFloat] = Field(default_factory=dict, max_length=32)
    algorithm_version: Version
    limitations: list[Code] = Field(default_factory=list, max_length=16)


class SceneNode(Phase10Model):
    schema_version: Literal["cad.scene-node/1"] = CAD_SCENE_NODE_SCHEMA_VERSION
    node_id: NodeId
    source_entity_id: PublicId
    entity_type: EntityType
    layer: str = Field(min_length=1, max_length=255)
    space: Literal["model", "paper"]
    bounds: Bounds2d | None = None
    geometry: SceneGeometry | None = None
    geometry_status: GeometryStatus
    fingerprint: Digest
    source_runtime: Code
    source_capabilities: list[Capability] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def _geometry_matches_status(self) -> "SceneNode":
        has_geometry = self.geometry is not None
        if self.geometry_status in {"exact", "bounded_projection"} and not has_geometry:
            raise ValueError("usable geometry status requires geometry")
        if self.geometry_status in {"unsupported", "unavailable", "invalid"} and has_geometry:
            raise ValueError("unusable geometry status forbids geometry")
        return self


class SceneRelation(Phase10Model):
    schema_version: Literal["cad.scene-relation/1"] = CAD_SCENE_RELATION_SCHEMA_VERSION
    relation_id: RelationId
    relation_type: RelationType
    source_node_ids: list[NodeId] = Field(min_length=2, max_length=8)
    directionality: Literal["symmetric", "directed"]
    evidence_strength: EvidenceStrength
    confidence: Confidence
    metrics: dict[Code, FiniteFloat] = Field(default_factory=dict, max_length=32)
    tolerance_used: PositiveFiniteFloat
    algorithm_version: Version
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=16)

    @field_validator("source_node_ids")
    @classmethod
    def _distinct_nodes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("relation nodes must be distinct")
        return value


class SceneContour(Phase10Model):
    schema_version: Literal["cad.scene-contour/1"] = CAD_SCENE_CONTOUR_SCHEMA_VERSION
    contour_id: ContourId
    source_node_ids: list[NodeId] = Field(min_length=1, max_length=4_096)
    closed: bool
    bounds: Bounds2d
    signed_area: FiniteFloat | None = None
    orientation: Literal["clockwise", "counterclockwise", "undefined"]
    algorithm_version: Version
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=16)


class SceneFeature(Phase10Model):
    schema_version: Literal["cad.scene-feature/1"] = CAD_SCENE_FEATURE_SCHEMA_VERSION
    feature_id: FeatureId
    feature_type: FeatureType
    source_node_ids: list[NodeId] = Field(min_length=1, max_length=256)
    source_relation_ids: list[RelationId] = Field(default_factory=list, max_length=256)
    confidence: Confidence
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=32)
    algorithm_version: Version
    limitations: list[Code] = Field(default_factory=list, max_length=16)


class SceneIssue(Phase10Model):
    schema_version: Literal["cad.scene-issue/1"] = CAD_SCENE_ISSUE_SCHEMA_VERSION
    issue_id: IssueId
    code: IssueCode
    severity: Literal["info", "warning", "error"]
    source_node_ids: list[NodeId] = Field(default_factory=list, max_length=256)
    source_relation_ids: list[RelationId] = Field(default_factory=list, max_length=256)
    message_key: Code
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=32)
    confidence: Confidence
    suggested_action: Code | None = None
    write_authority: Literal[False] = False


class SceneCounts(Phase10Model):
    nodes: int = Field(ge=0, le=10_000)
    relations: int = Field(ge=0, le=200_000)
    contours: int = Field(ge=0, le=20_000)
    features: int = Field(ge=0, le=50_000)
    issues: int = Field(ge=0, le=50_000)
    evidence: int = Field(ge=0, le=200_000)
    omitted: int = Field(default=0, ge=0, le=500_000)


class SceneResourceUris(Phase10Model):
    summary: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/summary$", max_length=256)
    nodes: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/nodes$", max_length=256)
    relations: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/relations$", max_length=256)
    contours: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/contours$", max_length=256)
    features: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/features$", max_length=256)
    issues: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/issues$", max_length=256)
    evidence: str = Field(pattern=r"^cad://scenes/[A-Za-z0-9._-]+/evidence$", max_length=256)


class SceneRoot(Phase10Model):
    schema_version: Literal["cad.scene/1"] = CAD_SCENE_SCHEMA_VERSION
    scene_id: Annotated[str, StringConstraints(pattern=r"^scn_[A-Za-z0-9_-]{16,120}$")]
    source_snapshot_id: PublicId
    device_id: PublicId
    document_id: PublicId
    document_revision: str = Field(pattern=r"^[!-~]{1,128}$")
    space: Literal["model"]
    projection_version: Literal["cad.entity-projection/2"]
    engine_version: Version
    profile_id: Literal["mechanical-2d/1"]
    tolerance_profile: ToleranceProfile
    source_digest: Digest
    scene_digest: Digest
    complete: bool
    truncation_reasons: list[Code] = Field(default_factory=list, max_length=16)
    counts: SceneCounts
    capabilities: list[Capability] = Field(default_factory=list, max_length=64)
    warnings: list[Code] = Field(default_factory=list, max_length=64)
    source_snapshot_available: bool
    resource_uris: SceneResourceUris

    @model_validator(mode="after")
    def _completeness_is_explicit(self) -> "SceneRoot":
        if self.complete and (self.truncation_reasons or self.counts.omitted):
            raise ValueError("complete scene cannot report truncation")
        if not self.complete and not self.truncation_reasons:
            raise ValueError("partial scene requires a truncation reason")
        return self


class CadBuildSceneInput(Phase10Model):
    source_snapshot_id: PublicId
    analysis_profile: Literal["mechanical-2d/1"] = "mechanical-2d/1"
    space: Literal["model"] = "model"
    include_sections: list[SceneSection] = Field(
        default_factory=lambda: [
            "nodes",
            "relations",
            "contours",
            "features",
            "issues",
            "evidence",
        ],
        min_length=1,
        max_length=6,
    )
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("include_sections")
    @classmethod
    def _canonical_sections(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("include_sections contains duplicates")
        return sorted(value)

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_key(cls, value: str) -> str:
        if any(character.isspace() for character in value) or not re.fullmatch(
            r"[A-Za-z0-9._:-]+", value
        ):
            raise ValueError("idempotency_key is malformed")
        return value


class CadBuildSceneOutput(Phase10Model):
    contract_version: Literal["cad.mcp/1.6"] = CAD_SCENE_PUBLIC_CONTRACT_VERSION
    correlation_id: PublicId
    scene: SceneRoot
    reused: bool


class CadQuerySceneInput(Phase10Model):
    scene_id: Annotated[str, StringConstraints(pattern=r"^scn_[A-Za-z0-9_-]{16,120}$")]
    section: SceneSection
    entity_types: list[EntityType] = Field(default_factory=list, max_length=16)
    relation_types: list[RelationType] = Field(default_factory=list, max_length=16)
    feature_types: list[FeatureType] = Field(default_factory=list, max_length=16)
    issue_codes: list[IssueCode] = Field(default_factory=list, max_length=16)
    source_entity_ids: list[PublicId] = Field(default_factory=list, max_length=64)
    confidence_min: Confidence | None = None
    cursor: str | None = Field(default=None, pattern=_CURSOR_PATTERN, max_length=512)
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator(
        "entity_types",
        "relation_types",
        "feature_types",
        "issue_codes",
        "source_entity_ids",
    )
    @classmethod
    def _canonical_filters(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("scene filter contains duplicates")
        return sorted(value)

    @model_validator(mode="after")
    def _filters_match_section(self) -> "CadQuerySceneInput":
        section_filters = {
            "nodes": bool(self.entity_types),
            "relations": bool(self.relation_types),
            "features": bool(self.feature_types),
            "issues": bool(self.issue_codes),
        }
        if any(enabled for section, enabled in section_filters.items() if section != self.section):
            raise ValueError("scene filter does not apply to selected section")
        if self.confidence_min is not None and self.section not in {
            "relations",
            "features",
            "issues",
        }:
            raise ValueError("confidence filter does not apply to selected section")
        return self


SceneSectionItem = Annotated[
    SceneNode | SceneRelation | SceneContour | SceneFeature | SceneIssue | SceneEvidence,
    Field(discriminator="schema_version"),
]


class CadQuerySceneOutput(Phase10Model):
    contract_version: Literal["cad.mcp/1.6"] = CAD_SCENE_PUBLIC_CONTRACT_VERSION
    correlation_id: PublicId
    scene_id: Annotated[str, StringConstraints(pattern=r"^scn_[A-Za-z0-9_-]{16,120}$")]
    scene_digest: Digest
    section: SceneSection
    items: list[SceneSectionItem] = Field(max_length=200)
    total: int = Field(ge=0, le=500_000)
    next_cursor: str | None = Field(default=None, pattern=_CURSOR_PATTERN, max_length=512)
    resource_uri: str = Field(
        pattern=(
            r"^cad://scenes/[A-Za-z0-9._-]+/"
            r"(?:nodes|relations|contours|features|issues|evidence)"
            r"(?:\?[A-Za-z0-9._~=&%-]{1,512})?$"
        ),
        max_length=768,
    )

    @model_validator(mode="after")
    def _items_match_section(self) -> "CadQuerySceneOutput":
        expected = {
            "nodes": CAD_SCENE_NODE_SCHEMA_VERSION,
            "relations": CAD_SCENE_RELATION_SCHEMA_VERSION,
            "contours": CAD_SCENE_CONTOUR_SCHEMA_VERSION,
            "features": CAD_SCENE_FEATURE_SCHEMA_VERSION,
            "issues": CAD_SCENE_ISSUE_SCHEMA_VERSION,
            "evidence": CAD_SCENE_EVIDENCE_SCHEMA_VERSION,
        }[self.section]
        if any(item.schema_version != expected for item in self.items):
            raise ValueError("scene items do not match selected section")
        return self


def _domain_digest(domain: str, value: Any) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    _validate_bounded_json(payload)
    encoded = canonical_json({"domain": domain, "payload": payload}).encode("utf-8")
    if len(encoded) > _MAX_MODEL_BYTES:
        raise ValueError("scene digest payload exceeds byte limit")
    return "sha256:" + sha256(encoded).hexdigest()


def _canonical_semantic_payload(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        result = {
            item_key: _canonical_semantic_payload(item, key=item_key)
            for item_key, item in value.items()
        }
        if (
            result.get("schema_version") == CAD_SCENE_RELATION_SCHEMA_VERSION
            and result.get("directionality") == "symmetric"
            and isinstance(result.get("source_node_ids"), list)
        ):
            result["source_node_ids"] = sorted(result["source_node_ids"])
        return result
    if isinstance(value, (list, tuple)):
        result = [_canonical_semantic_payload(item) for item in value]
        id_fields = {
            "nodes": "node_id",
            "relations": "relation_id",
            "contours": "contour_id",
            "features": "feature_id",
            "issues": "issue_id",
            "evidence": "evidence_id",
            "projections": "source_entity_id",
            "entity_fingerprints": "source_entity_id",
        }
        id_field = id_fields.get(key or "")
        if id_field and all(isinstance(item, dict) for item in result):
            return sorted(
                result,
                key=lambda item: (
                    str(item.get(id_field, item.get("entity_id", ""))),
                    canonical_json(item),
                ),
            )
        if key in {
            "capabilities",
            "source_capabilities",
            "warnings",
            "truncation_reasons",
            "evidence_ids",
            "source_entity_ids",
            "source_relation_ids",
            "limitations",
            "include_sections",
        }:
            return sorted(result, key=canonical_json)
        return result
    return value


def _stable_id(prefix: str, domain: str, value: Any) -> str:
    return f"{prefix}_{_domain_digest(domain, value).removeprefix('sha256:')}"


def canonical_scene_node_id(source_entity_id: str) -> str:
    return _stable_id(
        "nod", "cad.scene-node-id/1", {"source_entity_id": source_entity_id}
    )


def canonical_scene_relation_id(
    *,
    relation_type: RelationType,
    directionality: Literal["symmetric", "directed"],
    source_node_ids: list[str],
    metrics: dict[str, float] | None = None,
) -> str:
    nodes = sorted(source_node_ids) if directionality == "symmetric" else source_node_ids
    return _stable_id(
        "rel",
        "cad.scene-relation-id/1",
        {
            "relation_type": relation_type,
            "directionality": directionality,
            "source_node_ids": nodes,
            "metrics": metrics or {},
        },
    )


def canonical_scene_contour_id(
    *, source_node_ids: list[str], algorithm_version: str
) -> str:
    return _stable_id(
        "ctr",
        "cad.scene-contour-id/1",
        {
            "source_node_ids": sorted(source_node_ids),
            "algorithm_version": algorithm_version,
        },
    )


def canonical_scene_feature_id(
    *, feature_type: FeatureType, source_evidence_ids: list[str], algorithm_version: str
) -> str:
    return _stable_id(
        "fea",
        "cad.scene-feature-id/1",
        {
            "feature_type": feature_type,
            "source_evidence_ids": sorted(source_evidence_ids),
            "algorithm_version": algorithm_version,
        },
    )


def canonical_scene_issue_id(
    *, issue_code: IssueCode, source_evidence_ids: list[str], detector_version: str
) -> str:
    return _stable_id(
        "iss",
        "cad.scene-issue-id/1",
        {
            "issue_code": issue_code,
            "source_evidence_ids": sorted(source_evidence_ids),
            "detector_version": detector_version,
        },
    )


def canonical_scene_evidence_id(
    *, evidence_type: str, source_entity_ids: list[str], algorithm_version: str
) -> str:
    return _stable_id(
        "evd",
        "cad.scene-evidence-id/1",
        {
            "evidence_type": evidence_type,
            "source_entity_ids": sorted(source_entity_ids),
            "algorithm_version": algorithm_version,
        },
    )


def canonical_scene_source_digest(value: BaseModel | dict[str, Any]) -> str:
    return _domain_digest("cad.scene-source/1", _canonical_semantic_payload(value))


def canonical_scene_digest(value: BaseModel | dict[str, Any]) -> str:
    return _domain_digest("cad.scene/1", _canonical_semantic_payload(value))


def phase10_scene_json_schema() -> dict[str, Any]:
    return SceneRoot.model_json_schema(mode="validation")


def phase10_build_scene_input_json_schema() -> dict[str, Any]:
    return CadBuildSceneInput.model_json_schema(mode="validation")


def phase10_build_scene_output_json_schema() -> dict[str, Any]:
    return CadBuildSceneOutput.model_json_schema(mode="validation")


def phase10_query_scene_input_json_schema() -> dict[str, Any]:
    return CadQuerySceneInput.model_json_schema(mode="validation")


def phase10_query_scene_output_json_schema() -> dict[str, Any]:
    return CadQuerySceneOutput.model_json_schema(mode="validation")
