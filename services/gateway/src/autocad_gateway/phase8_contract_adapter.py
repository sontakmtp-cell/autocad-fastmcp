"""Narrow adapter boundary to the independently-owned Phase 8 compiler.

The Gateway stores and binds compiler output, but it must not evaluate source
expressions or expand CAD Program operations itself.  The contracts package can
implement this protocol without importing Gateway infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol, runtime_checkable

from autocad_contracts import (
    ExecutionPins,
    canonical_hard_budgets,
    canonical_json,
    canonical_source_digest,
    compile_cad_program_v1,
    parse_execution_plan_v1,
    seal_cad_program_v1,
)


COMPILER_CORE_OPERATION_PACK = "compiler.core/1"
CREATE_EQUIVALENT_OPERATION_PACK = "create-equivalent/1"
TRANSFORM_EXACT_OPERATION_PACK = "transform.exact/1"
_REVISION_REQUEST_DOMAIN = "cad.program.revision-request/1"
_REVISION_CONFLICT_DOMAIN = "cad.program.revision-conflicts/1"
_PATCHABLE_SOURCE_FIELDS = frozenset(
    {
        "variables",
        "operations",
        "budgets",
        "required_capabilities",
        "validation_profiles",
        "artifact_refs",
        "component_refs",
    }
)


def _domain_digest(domain: str, value: Any) -> str:
    material = canonical_json({"domain": domain, "value": value}).encode("utf-8")
    return "sha256:" + sha256(material).hexdigest()


def _snapshot_entities(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entity in snapshot.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        for key in ("ref_id", "entity_id", "handle"):
            value = entity.get(key)
            if isinstance(value, str) and value:
                if value in indexed and indexed[value] != entity:
                    raise ValueError("snapshot ref is ambiguous")
                indexed[value] = entity
    return indexed


def _snapshot_document_id(snapshot: dict[str, Any]) -> str | None:
    drawing = snapshot.get("drawing")
    if not isinstance(drawing, dict):
        return None
    value = drawing.get("document_id")
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class Phase8CompilerSettings:
    """Trusted deployment pins; none of these values come from program source."""

    compiler_package_hash: str
    runtime_id: str
    host_family: str
    host_version: str
    package_id: str
    package_version: str
    package_hash: str
    capability_manifest_hash: str
    operation_registry_version: str
    operation_registry_hash: str
    policy_version: str
    rollout_policy_digest: str

    def execution_pins(self) -> ExecutionPins:
        return ExecutionPins.model_validate(
            {
                "runtime_id": self.runtime_id,
                "runtime_role": "primary",
                "host_family": self.host_family,
                "host_version": self.host_version,
                "package_id": self.package_id,
                "package_version": self.package_version,
                "package_hash": self.package_hash,
                "capability_manifest_hash": self.capability_manifest_hash,
                "operation_registry_version": self.operation_registry_version,
                "operation_registry_hash": self.operation_registry_hash,
                "policy_version": self.policy_version,
                "rollout_policy_digest": self.rollout_policy_digest,
            }
        )

@dataclass(frozen=True)
class CompiledProgram:
    """Verified output returned by the shared deterministic compiler adapter."""

    source: dict[str, Any]
    source_digest: str
    semantic_digest: str
    plan: dict[str, Any]
    plan_digest: str
    expansion_digest: str
    effect_manifest: dict[str, Any]
    effect_digest: str
    target_set_digest: str
    reference_digest: str
    risk_class: str
    trusted_effect_summary: tuple[dict[str, Any], ...]
    compiler_id: str
    compiler_version: str
    compiler_hash: str
    hard_budgets: dict[str, int]
    required_capabilities: tuple[str, ...]
    operation_packs: tuple[str, ...]
    validation_profiles: tuple[str, ...]
    runtime_pins: dict[str, str]
    checkpoint_strategy: str
    create_count: int
    modify_count: int
    erase_count: int


@dataclass(frozen=True)
class RevisionMaterialization:
    """Full candidate source plus explicit conflicts from a patch/rebase adapter."""

    source: dict[str, Any]
    source_digest: str
    semantic_digest: str
    request_digest: str
    conflicts_digest: str | None = None
    conflicts: tuple[dict[str, Any], ...] = ()


@runtime_checkable
class Phase8CompilerPort(Protocol):
    """Compiler-owned behavior used by Gateway without duplicating semantics."""

    def compile(
        self,
        source: dict[str, Any],
        *,
        materialized_target_refs: list[dict[str, Any]] | None = None,
        materialized_owner_id: str | None = None,
    ) -> CompiledProgram:
        ...


@runtime_checkable
class Phase8RevisionPort(Protocol):
    """Contract-owned patch/rebase materialization and conflict detection."""

    def apply_patch(
        self,
        source: dict[str, Any],
        patch: dict[str, Any],
    ) -> RevisionMaterialization:
        ...

    def rebase(
        self,
        source: dict[str, Any],
        *,
        old_snapshot: dict[str, Any],
        new_snapshot: dict[str, Any],
    ) -> RevisionMaterialization:
        ...


class AutocadContractsPhase8Compiler:
    """Concrete adapter around the canonical contracts-owned compiler."""

    def __init__(self, settings: Phase8CompilerSettings) -> None:
        self.settings = settings
        # Validate all trusted pins at composition time, before accepting source.
        self.pins = settings.execution_pins()

    def compile(
        self,
        source: dict[str, Any],
        *,
        materialized_target_refs: list[dict[str, Any]] | None = None,
        materialized_owner_id: str | None = None,
    ) -> CompiledProgram:
        sealed = seal_cad_program_v1(source)
        plan = compile_cad_program_v1(
            sealed,
            self.pins,
            compiler_package_hash=self.settings.compiler_package_hash,
            materialized_target_refs=materialized_target_refs,
            materialized_owner_id=materialized_owner_id,
        )
        # Parse once more through the public plan parser. This prevents adapter
        # field mapping from persisting an object the canonical verifier rejects.
        parsed = parse_execution_plan_v1(plan.model_dump(mode="json"))
        source_value = sealed.model_dump(mode="json", exclude_none=True)
        plan_value = parsed.model_dump(mode="json", exclude_none=True)
        effect_value = parsed.effect_manifest.model_dump(mode="json")
        operation_packs = [COMPILER_CORE_OPERATION_PACK]
        effect_classes = {
            entry.effect_class for entry in parsed.effect_manifest.entries
        }
        if effect_classes & {"create_only", "ensure_non_entity"}:
            operation_packs.append(CREATE_EQUIVALENT_OPERATION_PACK)
        if "modify_in_place" in effect_classes:
            operation_packs.append(TRANSFORM_EXACT_OPERATION_PACK)
        ref_material = {
            "artifact_refs": plan_value["artifact_refs"],
            "component_refs": plan_value["component_refs"],
        }
        reference_digest = (
            "sha256:"
            + sha256(canonical_json(ref_material).encode("utf-8")).hexdigest()
        )
        summary: list[dict[str, Any]] = []
        if parsed.effect_manifest.creates:
            summary.append(
                {
                    "kind": "create_entities",
                    "count": parsed.effect_manifest.creates,
                    "summary": (
                        f"Create {parsed.effect_manifest.creates} bounded drawing "
                        "entities from the sealed CAD Program."
                    ),
                }
            )
        if parsed.effect_manifest.ensures_non_entity:
            summary.append(
                {
                    "kind": "ensure_layers",
                    "count": parsed.effect_manifest.ensures_non_entity,
                    "summary": (
                        f"Ensure {parsed.effect_manifest.ensures_non_entity} bounded "
                        "drawing layers from the sealed CAD Program."
                    ),
                }
            )
        if parsed.effect_manifest.modifies:
            summary.append(
                {
                    "kind": "modify_entities",
                    "count": parsed.effect_manifest.modifies,
                    "summary": (
                        f"Modify {parsed.effect_manifest.modifies} exact "
                        "allowlisted drawing entities from the sealed CAD Program."
                    ),
                }
            )
        return CompiledProgram(
            source=source_value,
            source_digest=sealed.semantic_digest,
            semantic_digest=sealed.semantic_digest,
            plan=plan_value,
            plan_digest=parsed.execution_plan_digest,
            expansion_digest=parsed.expansion_digest,
            effect_manifest=effect_value,
            effect_digest=parsed.effect_manifest_digest,
            target_set_digest=parsed.target_refs_digest,
            reference_digest=reference_digest,
            risk_class=parsed.effect_manifest.risk_floor,
            trusted_effect_summary=tuple(summary),
            compiler_id=parsed.compiler.compiler_id,
            compiler_version=parsed.compiler.compiler_version,
            compiler_hash=parsed.compiler.compiler_package_hash,
            hard_budgets=canonical_hard_budgets(parsed.budgets),
            required_capabilities=tuple(parsed.required_capabilities),
            operation_packs=tuple(operation_packs),
            validation_profiles=tuple(parsed.validation_profiles),
            runtime_pins=parsed.execution_pins.model_dump(mode="json"),
            checkpoint_strategy=(
                "cad.rollback.checkpoint/2"
                if parsed.effect_manifest.modifies
                else "cad.rollback.checkpoint/1"
            ),
            create_count=parsed.effect_manifest.creates,
            modify_count=parsed.effect_manifest.modifies,
            erase_count=parsed.effect_manifest.erases,
        )


class AutocadContractsPhase8Revision:
    """Materialize bounded immutable patch/rebase revisions from trusted inputs."""

    def apply_patch(
        self,
        source: dict[str, Any],
        patch: dict[str, Any],
    ) -> RevisionMaterialization:
        if not isinstance(patch, dict) or not patch:
            raise ValueError("patch changes are required")
        if not set(patch).issubset(_PATCHABLE_SOURCE_FIELDS):
            raise ValueError("patch contains a non-editable field")
        parent = seal_cad_program_v1(source)
        candidate = parent.model_dump(mode="json", exclude_none=True)
        candidate.update(patch)
        candidate["program_revision"] = parent.program_revision + 1
        candidate["parent_revision"] = parent.program_revision
        sealed = seal_cad_program_v1(candidate)
        value = sealed.model_dump(mode="json", exclude_none=True)
        request = {
            "kind": "patch",
            "program_id": parent.program_id,
            "source_revision": parent.program_revision,
            "changes": patch,
        }
        return RevisionMaterialization(
            source=value,
            source_digest=canonical_source_digest(value),
            semantic_digest=sealed.semantic_digest,
            request_digest=_domain_digest(_REVISION_REQUEST_DOMAIN, request),
        )

    def rebase(
        self,
        source: dict[str, Any],
        *,
        old_snapshot: dict[str, Any],
        new_snapshot: dict[str, Any],
    ) -> RevisionMaterialization:
        parent = seal_cad_program_v1(source)
        if (
            old_snapshot.get("snapshot_id") != parent.source_snapshot_id
            or old_snapshot.get("device_id") != parent.device_id
            or _snapshot_document_id(old_snapshot) != parent.document_id
        ):
            raise ValueError("old snapshot does not match source binding")
        if (
            new_snapshot.get("device_id") != parent.device_id
            or _snapshot_document_id(new_snapshot) != parent.document_id
        ):
            raise ValueError("new snapshot does not match source binding")
        new_snapshot_id = new_snapshot.get("snapshot_id")
        new_revision = new_snapshot.get("document_revision")
        if not isinstance(new_snapshot_id, str) or not isinstance(new_revision, str):
            raise ValueError("new snapshot binding is incomplete")

        old_entities = _snapshot_entities(old_snapshot)
        new_entities = _snapshot_entities(new_snapshot)
        target_ids = sorted(
            {
                operation.target_ref_id
                for operation in parent.operations
                if getattr(operation, "target_ref_id", None) is not None
            }
        )
        conflicts: list[dict[str, Any]] = []
        for ref_id in target_ids:
            old_entity = old_entities.get(ref_id)
            new_entity = new_entities.get(ref_id)
            if old_entity is None:
                conflicts.append({"code": "old_target_missing", "ref_id": ref_id})
                continue
            if new_entity is None:
                conflicts.append({"code": "target_missing", "ref_id": ref_id})
                continue
            old_type = old_entity.get("entity_type") or old_entity.get("type")
            new_type = new_entity.get("entity_type") or new_entity.get("type")
            if old_type != new_type:
                conflicts.append(
                    {
                        "code": "target_type_changed",
                        "ref_id": ref_id,
                        "old_type": old_type,
                        "new_type": new_type,
                    }
                )
                continue
            if old_entity.get("fingerprint") != new_entity.get("fingerprint"):
                conflicts.append(
                    {"code": "target_fingerprint_changed", "ref_id": ref_id}
                )

        candidate = parent.model_dump(mode="json", exclude_none=True)
        candidate.update(
            {
                "program_revision": parent.program_revision + 1,
                "parent_revision": parent.program_revision,
                "source_snapshot_id": new_snapshot_id,
                "expected_document_revision": new_revision,
            }
        )
        sealed = seal_cad_program_v1(candidate)
        value = sealed.model_dump(mode="json", exclude_none=True)
        request = {
            "kind": "rebase",
            "program_id": parent.program_id,
            "source_revision": parent.program_revision,
            "old_snapshot_id": old_snapshot["snapshot_id"],
            "new_snapshot_id": new_snapshot_id,
        }
        conflicts_value = tuple(conflicts)
        return RevisionMaterialization(
            source=value,
            source_digest=canonical_source_digest(value),
            semantic_digest=sealed.semantic_digest,
            request_digest=_domain_digest(_REVISION_REQUEST_DOMAIN, request),
            conflicts_digest=(
                _domain_digest(_REVISION_CONFLICT_DOMAIN, conflicts)
                if conflicts
                else None
            ),
            conflicts=conflicts_value,
        )
