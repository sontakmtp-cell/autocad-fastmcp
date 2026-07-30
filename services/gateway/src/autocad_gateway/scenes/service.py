"""Phase 10 scene application service."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from autocad_contracts import (
    CadBuildSceneInput,
    CadBuildSceneOutput,
    CadQuerySceneInput,
    CadQuerySceneOutput,
    SceneRoot,
    canonical_scene_source_digest,
)
from cad_core.scene import SceneBuildContext, SceneBudgets, build_scene

from ..services import GatewayError
from .cursors import decode_cursor, encode_cursor
from .public_projection import project_artifact
from .repository import SceneRepository, SceneRepositoryConflict


class SceneApplicationService:
    def __init__(
        self,
        repository: SceneRepository,
        snapshot_repository: Any,
        *,
        cursor_secret: bytes | None,
        retention_hours: int = 24,
        mechanical_features_enabled: bool = False,
        annotation_links_enabled: bool = False,
    ) -> None:
        if cursor_secret is not None and len(cursor_secret) < 32:
            raise ValueError("scene cursor secret must be at least 32 bytes")
        self.repository = repository
        self.snapshot_repository = snapshot_repository
        self.cursor_secret = cursor_secret
        self.retention_hours = retention_hours
        self.mechanical_features_enabled = mechanical_features_enabled
        self.annotation_links_enabled = annotation_links_enabled

    async def build(
        self,
        owner_subject: str,
        request: CadBuildSceneInput,
        correlation_id: str,
    ) -> CadBuildSceneOutput:
        snapshot, artifact = await self._build_artifact(owner_subject, request)
        revision = str(snapshot["document_revision"])
        drawing = snapshot.get("drawing") if isinstance(snapshot.get("drawing"), dict) else {}
        document_id = _document_id(drawing, snapshot)
        if (
            artifact.context.device_id != str(snapshot["device_id"])
            or artifact.context.document_id != document_id
            or artifact.context.document_revision != revision
        ):
            raise GatewayError("backend_error")
        scene_id = "scn_" + uuid.uuid4().hex
        effective_sections = sorted(
            {
                *request.include_sections,
                *(
                    {"nodes", "evidence"}
                    if any(
                        section
                        in {"relations", "contours", "features", "issues"}
                        for section in request.include_sections
                    )
                    else set()
                ),
            }
        )
        root, sections, section_digests = project_artifact(
            artifact,
            scene_id=scene_id,
            source_snapshot_available=True,
            include_sections=effective_sections,
            mechanical_features_enabled=self.mechanical_features_enabled,
        )
        request_hash = canonical_scene_source_digest(request)
        build_options_digest = canonical_scene_source_digest(
            {
                "include_sections": list(request.include_sections),
                "effective_sections": effective_sections,
                "mechanical_features_enabled": self.mechanical_features_enabled,
                "annotation_links_enabled": self.annotation_links_enabled,
            }
        )
        tolerance_digest = canonical_scene_source_digest(
            root.tolerance_profile.model_dump(mode="json")
        )
        try:
            record, reused = await self.repository.create(
                owner_subject=owner_subject,
                root=root.model_dump(mode="json"),
                sections=sections,
                request_hash=request_hash,
                idempotency_key=request.idempotency_key,
                tolerance_digest=tolerance_digest,
                build_options_digest=build_options_digest,
                section_digests=section_digests,
                correlation_id=correlation_id,
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=self.retention_hours)
                ).isoformat(),
            )
        except SceneRepositoryConflict as error:
            raise GatewayError(str(error)) from error
        return CadBuildSceneOutput(
            correlation_id=correlation_id,
            scene=SceneRoot.model_validate(record["root"]),
            reused=reused,
        )

    async def _build_artifact(
        self,
        owner_subject: str,
        request: CadBuildSceneInput,
    ) -> tuple[dict[str, Any], Any]:
        snapshot = await self.snapshot_repository.get_snapshot(
            owner_subject, request.source_snapshot_id
        )
        if snapshot is None:
            raise GatewayError("not_found")
        entities = snapshot.get("entities")
        if not isinstance(entities, list):
            raise GatewayError("backend_error")
        revision = str(snapshot.get("document_revision", ""))
        if not revision:
            raise GatewayError("backend_error")
        drawing = snapshot.get("drawing") if isinstance(snapshot.get("drawing"), dict) else {}
        document_id = _document_id(drawing, snapshot)
        source_capabilities = sorted(
            {
                str(capability)
                for entity in entities
                if isinstance(entity, dict)
                for capability in entity.get("source_capabilities", [])
                if isinstance(capability, str)
            }
        )
        context = SceneBuildContext(
            source_snapshot_id=request.source_snapshot_id,
            device_id=str(snapshot["device_id"]),
            document_id=document_id,
            document_revision=revision,
            space=request.space,
            profile_id=request.analysis_profile,
            source_capabilities=tuple(source_capabilities),
            drawing_units=str(drawing.get("units") or "unitless")[:32],
        )
        try:
            artifact = build_scene(entities, context, budgets=SceneBudgets())
        except ValueError as error:
            raise GatewayError("invalid_request") from error
        except Exception as error:
            if getattr(error, "code", None) == "scene_budget_exceeded":
                raise GatewayError("scene_budget_exceeded") from error
            raise
        return snapshot, artifact

    async def query(
        self,
        owner_subject: str,
        request: CadQuerySceneInput,
        correlation_id: str,
    ) -> CadQuerySceneOutput:
        record = await self.repository.get(owner_subject, request.scene_id)
        if record is None:
            raise GatewayError("not_found")
        items = await self.repository.get_section(
            owner_subject, request.scene_id, request.section
        )
        if items is None:
            raise GatewayError("not_found")
        filters = _filters(request)
        evidence_by_id: dict[str, dict[str, Any]] = {}
        if request.source_entity_ids and request.section != "nodes":
            evidence_items = await self.repository.get_section(
                owner_subject, request.scene_id, "evidence"
            )
            evidence_by_id = {
                str(item.get("evidence_id")): item
                for item in evidence_items or []
                if isinstance(item, dict)
            }
        selected = [
            item
            for item in items
            if _matches(item, request, evidence_by_id=evidence_by_id)
        ]
        offset = 0
        if request.cursor:
            if self.cursor_secret is None:
                raise GatewayError("feature_disabled")
            try:
                offset = decode_cursor(
                    request.cursor,
                    secret=self.cursor_secret,
                    owner_subject=owner_subject,
                    scene_id=request.scene_id,
                    section=request.section,
                    filters=filters,
                    projection_version=record["projection_version"],
                )
            except ValueError:
                raise GatewayError("invalid_request") from None
        if offset > len(selected):
            raise GatewayError("invalid_request")
        page = selected[offset : offset + request.limit]
        next_cursor = None
        if offset + request.limit < len(selected):
            if self.cursor_secret is None:
                raise GatewayError("feature_disabled")
            next_cursor = encode_cursor(
                secret=self.cursor_secret,
                owner_subject=owner_subject,
                scene_id=request.scene_id,
                section=request.section,
                filters=filters,
                offset=offset + request.limit,
                projection_version=record["projection_version"],
            )
        return CadQuerySceneOutput(
            correlation_id=correlation_id,
            scene_id=request.scene_id,
            scene_digest=record["scene_digest"],
            section=request.section,
            items=page,
            total=len(selected),
            next_cursor=next_cursor,
            resource_uri=f"cad://scenes/{request.scene_id}/{request.section}",
        )

    async def summary(self, owner_subject: str, scene_id: str) -> dict[str, Any]:
        record = await self.repository.get(owner_subject, scene_id)
        if record is None:
            raise GatewayError("not_found")
        root = dict(record["root"])
        root["source_snapshot_available"] = (
            await self.snapshot_repository.get_snapshot(
                owner_subject, record["source_snapshot_id"]
            )
            is not None
        )
        return SceneRoot.model_validate(root).model_dump(mode="json")

    async def list(self, owner_subject: str, *, limit: int = 100) -> dict[str, Any]:
        records = await self.repository.list(owner_subject, limit=limit)
        return {
            "scenes": [item["root"] for item in records]
        }

    async def source_digest(
        self,
        *,
        owner_subject: str,
        device_id: str,
        source_snapshot_id: str,
        document_revision: str,
        analysis_profile: str,
    ) -> str:
        request = CadBuildSceneInput(
            source_snapshot_id=source_snapshot_id,
            analysis_profile=analysis_profile,
            idempotency_key="workflow-source-digest",
        )
        snapshot, artifact = await self._build_artifact(owner_subject, request)
        if (
            str(snapshot["device_id"]) != device_id
            or str(snapshot["document_revision"]) != document_revision
        ):
            raise GatewayError("binding_mismatch")
        return str(artifact.source_digest)

    async def dispatch(
        self,
        action_kind: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        owner_subject = str(payload.get("owner_subject", ""))
        if not owner_subject:
            raise GatewayError("invalid_request")
        if action_kind == "build_scene":
            built = await self.build(
                owner_subject,
                CadBuildSceneInput(
                    source_snapshot_id=payload["source_snapshot_id"],
                    analysis_profile=payload["analysis_profile"],
                    space=payload.get("space", "model"),
                    include_sections=payload["include_sections"],
                    idempotency_key=idempotency_key,
                ),
                _workflow_correlation_id(idempotency_key),
            )
            result = built.scene.model_dump(mode="json")
        elif action_kind == "query_scene":
            record = await self.repository.get(owner_subject, payload["scene_id"])
            if record is None:
                raise GatewayError("not_found")
            queried = await self.query(
                owner_subject,
                CadQuerySceneInput(
                    scene_id=payload["scene_id"],
                    section=payload["section"],
                    limit=payload.get("limit", 100),
                ),
                _workflow_correlation_id(idempotency_key),
            )
            result = {
                **queried.model_dump(mode="json"),
                **_workflow_scene_binding(record),
            }
        elif action_kind == "validate_scene":
            record = await self.repository.get(owner_subject, payload["scene_id"])
            if record is None:
                raise GatewayError("not_found")
            result = {"valid": True, **_workflow_scene_binding(record)}
        else:
            raise GatewayError("workflow_action_invalid")
        _validate_workflow_binding(result, payload)
        return result

    async def reconcile(
        self,
        action_kind: str,
        child_ref: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        del action_kind, child_ref, idempotency_key
        return None


def _document_id(drawing: dict[str, Any], snapshot: dict[str, Any]) -> str:
    candidate = drawing.get("document_id")
    if isinstance(candidate, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", candidate
    ):
        return candidate
    value = {
        "device_id": snapshot.get("device_id"),
        "document_revision": snapshot.get("document_revision"),
        "drawing_name_digest": hashlib.sha256(
            str(drawing.get("name", "")).encode("utf-8")
        ).hexdigest(),
    }
    return "doc_" + canonical_scene_source_digest(value)[7:39]


def _workflow_correlation_id(idempotency_key: str) -> str:
    return "wf_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]


def _workflow_scene_binding(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": record["scene_id"],
        "scene_digest": record["scene_digest"],
        "source_digest": record["source_digest"],
        "source_snapshot_id": record["source_snapshot_id"],
        "document_revision": record["document_revision"],
    }


def _validate_workflow_binding(
    result: dict[str, Any], payload: dict[str, Any]
) -> None:
    for key in (
        "scene_id",
        "scene_digest",
        "source_digest",
        "source_snapshot_id",
        "document_revision",
    ):
        expected = payload.get(key)
        if expected is not None and result.get(key) != expected:
            raise GatewayError("binding_mismatch")


def _filters(request: CadQuerySceneInput) -> dict[str, Any]:
    return {
        "entity_types": list(request.entity_types),
        "relation_types": list(request.relation_types),
        "feature_types": list(request.feature_types),
        "issue_codes": list(request.issue_codes),
        "source_entity_ids": list(request.source_entity_ids),
        "confidence_min": request.confidence_min,
    }


def _matches(
    item: dict[str, Any],
    request: CadQuerySceneInput,
    *,
    evidence_by_id: dict[str, dict[str, Any]],
) -> bool:
    if request.entity_types and item.get("entity_type") not in request.entity_types:
        return False
    if request.relation_types and item.get("relation_type") not in request.relation_types:
        return False
    if request.feature_types and item.get("feature_type") not in request.feature_types:
        return False
    if request.issue_codes and item.get("code") not in request.issue_codes:
        return False
    if request.source_entity_ids:
        entity_ids = set()
        if item.get("source_entity_id") is not None:
            entity_ids.add(str(item["source_entity_id"]))
        entity_ids.update(str(value) for value in item.get("source_entity_ids", []))
        for evidence_id in item.get("evidence_ids", []):
            evidence = evidence_by_id.get(str(evidence_id), {})
            entity_ids.update(
                str(value) for value in evidence.get("source_entity_ids", [])
            )
        if not entity_ids.intersection(request.source_entity_ids):
            return False
    if (
        request.confidence_min is not None
        and float(item.get("confidence", 0.0)) < request.confidence_min
    ):
        return False
    return True
