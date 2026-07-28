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
    compile_cad_program_v1,
    parse_execution_plan_v1,
    seal_cad_program_v1,
)


CREATE_CORE_OPERATION_PACK = "create.core/1"


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

    def compile(self, source: dict[str, Any]) -> CompiledProgram:
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

    def compile(self, source: dict[str, Any]) -> CompiledProgram:
        sealed = seal_cad_program_v1(source)
        plan = compile_cad_program_v1(
            sealed,
            self.pins,
            compiler_package_hash=self.settings.compiler_package_hash,
        )
        # Parse once more through the public plan parser. This prevents adapter
        # field mapping from persisting an object the canonical verifier rejects.
        parsed = parse_execution_plan_v1(plan.model_dump(mode="json"))
        source_value = sealed.model_dump(mode="json", exclude_none=True)
        plan_value = parsed.model_dump(mode="json", exclude_none=True)
        effect_value = parsed.effect_manifest.model_dump(mode="json")
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
            operation_packs=(CREATE_CORE_OPERATION_PACK,),
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
