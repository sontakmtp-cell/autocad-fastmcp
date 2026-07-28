"""Narrow adapter boundary to the independently-owned Phase 8 compiler.

The Gateway stores and binds compiler output, but it must not evaluate source
expressions or expand CAD Program operations itself.  The contracts package can
implement this protocol without importing Gateway infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


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
