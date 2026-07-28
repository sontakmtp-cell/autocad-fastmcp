"""Gateway-owned Phase 8 orchestration without compiler semantics."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable

from autocad_contracts import canonical_json

from .infrastructure.sqlite.phase8_repository import Phase8Repository
from .infrastructure.sqlite.repositories import RepositoryConflict
from .phase8_contract_adapter import (
    COMPILER_CORE_OPERATION_PACK,
    CREATE_EQUIVALENT_OPERATION_PACK,
    CompiledProgram,
    Phase8CompilerPort,
    Phase8RevisionPort,
    TRANSFORM_EXACT_OPERATION_PACK,
)


_DESTRUCTIVE_PACK_MARKERS = (
    "delete",
    "erase",
    "trim",
    "fillet",
    "chamfer",
    "topology",
)


def _capability_operation_pack(capability: str) -> str:
    if capability == "cad.program.v1.compile":
        return COMPILER_CORE_OPERATION_PACK
    if capability.startswith("cad.op.move."):
        return TRANSFORM_EXACT_OPERATION_PACK
    return CREATE_EQUIVALENT_OPERATION_PACK


def phase8_binding_digest(plan: dict[str, Any]) -> str:
    """Bind Phase 7 approval to exact source/compiler/plan/effect material."""

    value = {
        "source_digest": plan["source_digest"],
        "semantic_digest": plan["semantic_digest"],
        "compiler_hash": plan["compiler_hash"],
        "plan_digest": plan["plan_digest"],
        "expansion_digest": plan["expansion_digest"],
        "effect_digest": plan["effect_digest"],
        "target_set_digest": plan["target_set_digest"],
        "reference_digest": plan["reference_digest"],
        "risk_class": plan["risk_class"],
        "trusted_effect_summary": plan["trusted_effect_summary"],
        "rollout_policy_digest": plan["rollout_policy_digest"],
        "rollout_policy_epoch": plan["rollout_policy_epoch"],
    }
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Phase8FeatureFlags:
    source_enabled: bool = False
    compiler_enabled: bool = False
    create_pack_enabled: bool = False
    transform_pack_enabled: bool = False
    topology_pack_enabled: bool = False
    delete_pack_enabled: bool = False
    checkpoint_v2_enabled: bool = False
    scoped_rollback_revalidation_enabled: bool = False
    lt_portable_write_enabled: bool = False
    operation_pack_allowlist: tuple[str, ...] = ()
    rollout_policy_digest: str | None = None
    rollout_policy_epoch: int = 0


def canonical_rollout_policy_digest(flags: Phase8FeatureFlags) -> str:
    value = {
        "schema_version": "cad.rollout-policy/1",
        "epoch": flags.rollout_policy_epoch,
        "source_enabled": flags.source_enabled,
        "compiler_enabled": flags.compiler_enabled,
        "create_pack_enabled": flags.create_pack_enabled,
        "transform_pack_enabled": flags.transform_pack_enabled,
        "topology_pack_enabled": flags.topology_pack_enabled,
        "delete_pack_enabled": flags.delete_pack_enabled,
        "checkpoint_v2_enabled": flags.checkpoint_v2_enabled,
        "scoped_rollback_revalidation_enabled": (
            flags.scoped_rollback_revalidation_enabled
        ),
        "lt_portable_write_enabled": flags.lt_portable_write_enabled,
        "operation_pack_allowlist": sorted(set(flags.operation_pack_allowlist)),
    }
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Phase8GatewayService:
    """Persist compiler output and enforce Gateway release policy."""

    def __init__(
        self,
        repository: Phase8Repository,
        flags: Phase8FeatureFlags,
        *,
        compiler: Phase8CompilerPort | None = None,
        revision_adapter: Phase8RevisionPort | None = None,
    ) -> None:
        self.repository = repository
        self.flags = flags
        self.compiler = compiler
        self.revision_adapter = revision_adapter
        self.allowed_packs = frozenset(flags.operation_pack_allowlist)

    async def prepare_root(
        self,
        *,
        owner_subject: str,
        program_id: str,
        device_id: str,
        document_id: str,
        source_snapshot_id: str,
        expected_document_revision: str,
        source: dict[str, Any],
        materialized_target_refs: list[dict[str, Any]] | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        compilation = self._compile(
            source,
            materialized_target_refs=materialized_target_refs,
            materialized_owner_id=(
                owner_subject if materialized_target_refs is not None else None
            ),
        )
        self._require_source_binding(
            compilation,
            program_id=program_id,
            device_id=device_id,
            document_id=document_id,
            source_snapshot_id=source_snapshot_id,
            expected_document_revision=expected_document_revision,
        )
        canonical_plan_id = compilation.plan.get("plan_id")
        if not isinstance(canonical_plan_id, str) or not canonical_plan_id:
            canonical_plan_id = plan_id
        if plan_id is not None and canonical_plan_id != plan_id:
            raise RepositoryConflict("plan_id_mismatch")
        revision, _ = await self.repository.create_revision(
            owner_subject=owner_subject,
            program_id=program_id,
            revision=1,
            device_id=device_id,
            document_id=document_id,
            source_snapshot_id=source_snapshot_id,
            expected_document_revision=expected_document_revision,
            source=compilation.source,
            source_digest=compilation.source_digest,
            semantic_digest=compilation.semantic_digest,
            lineage_kind="root",
        )
        plan, _ = await self.repository.seal_plan(
            owner_subject=owner_subject,
            program_id=program_id,
            revision=1,
            compilation=compilation,
            rollout_policy_digest=self._rollout_policy_digest(),
            rollout_policy_epoch=self.flags.rollout_policy_epoch,
            plan_id=canonical_plan_id,
        )
        return {"revision": revision, "plan": plan}

    async def patch(
        self,
        *,
        owner_subject: str,
        program_id: str,
        source_revision: int,
        patch: dict[str, Any],
        target_ref_resolver: (
            Callable[[dict[str, Any]], list[dict[str, Any]] | None] | None
        ) = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_revision_adapter()
        parent = await self.repository.get_revision(
            owner_subject, program_id, source_revision
        )
        if parent is None:
            raise RepositoryConflict("not_found")
        await self.repository.require_revision_revisable(
            owner_subject, program_id, source_revision
        )
        candidate_revision = await self.repository.next_revision(
            owner_subject, program_id
        )
        if candidate_revision != source_revision + 1:
            raise RepositoryConflict("revision_not_latest")
        materialized = self.revision_adapter.apply_patch(parent["source"], patch)
        compilation = None
        if not materialized.conflicts:
            materialized_target_refs = (
                target_ref_resolver(materialized.source)
                if target_ref_resolver is not None
                else None
            )
            compilation = self._compile(
                materialized.source,
                materialized_target_refs=materialized_target_refs,
                materialized_owner_id=(
                    owner_subject if materialized_target_refs is not None else None
                ),
            )
            self._require_compiler_digests(materialized, compilation)
        revision, _ = await self.repository.create_revision(
            owner_subject=owner_subject,
            program_id=program_id,
            revision=candidate_revision,
            device_id=parent["device_id"],
            document_id=parent["document_id"],
            source_snapshot_id=parent["source_snapshot_id"],
            expected_document_revision=parent["expected_document_revision"],
            source=materialized.source,
            source_digest=materialized.source_digest,
            semantic_digest=materialized.semantic_digest,
            lineage_kind="patch",
            parent_revision=source_revision,
            lineage_request_digest=materialized.request_digest,
        )
        if materialized.conflicts:
            if materialized.conflicts_digest is None:
                raise RepositoryConflict("conflict_digest_missing")
            report, _ = await self.repository.create_conflict_report(
                owner_subject=owner_subject,
                program_id=program_id,
                source_revision=source_revision,
                candidate_revision=candidate_revision,
                request_kind="patch",
                old_snapshot_id=parent["source_snapshot_id"],
                new_snapshot_id=None,
                request_digest=materialized.request_digest,
                conflicts_digest=materialized.conflicts_digest,
                conflicts=list(materialized.conflicts),
            )
            return {"revision": revision, "conflict_report": report, "plan": None}
        if compilation is None:
            raise RepositoryConflict("compiler_result_invalid")
        plan, _ = await self.repository.seal_plan(
            owner_subject=owner_subject,
            program_id=program_id,
            revision=candidate_revision,
            compilation=compilation,
            rollout_policy_digest=self._rollout_policy_digest(),
            rollout_policy_epoch=self.flags.rollout_policy_epoch,
            plan_id=plan_id,
        )
        return {"revision": revision, "conflict_report": None, "plan": plan}

    async def rebase(
        self,
        *,
        owner_subject: str,
        program_id: str,
        source_revision: int,
        old_snapshot: dict[str, Any],
        new_snapshot: dict[str, Any],
        target_ref_resolver: (
            Callable[[dict[str, Any]], list[dict[str, Any]] | None] | None
        ) = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_revision_adapter()
        parent = await self.repository.get_revision(
            owner_subject, program_id, source_revision
        )
        if parent is None:
            raise RepositoryConflict("not_found")
        await self.repository.require_revision_revisable(
            owner_subject, program_id, source_revision
        )
        candidate_revision = await self.repository.next_revision(
            owner_subject, program_id
        )
        if candidate_revision != source_revision + 1:
            raise RepositoryConflict("revision_not_latest")
        materialized = self.revision_adapter.rebase(
            parent["source"],
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
        )
        compilation = None
        if not materialized.conflicts:
            materialized_target_refs = (
                target_ref_resolver(materialized.source)
                if target_ref_resolver is not None
                else None
            )
            compilation = self._compile(
                materialized.source,
                materialized_target_refs=materialized_target_refs,
                materialized_owner_id=(
                    owner_subject if materialized_target_refs is not None else None
                ),
            )
            self._require_compiler_digests(materialized, compilation)
        revision, _ = await self.repository.create_revision(
            owner_subject=owner_subject,
            program_id=program_id,
            revision=candidate_revision,
            device_id=parent["device_id"],
            document_id=parent["document_id"],
            source_snapshot_id=new_snapshot["snapshot_id"],
            expected_document_revision=new_snapshot["document_revision"],
            source=materialized.source,
            source_digest=materialized.source_digest,
            semantic_digest=materialized.semantic_digest,
            lineage_kind="rebase",
            parent_revision=source_revision,
            base_revision=source_revision,
            lineage_request_digest=materialized.request_digest,
        )
        if materialized.conflicts:
            if materialized.conflicts_digest is None:
                raise RepositoryConflict("conflict_digest_missing")
            report, _ = await self.repository.create_conflict_report(
                owner_subject=owner_subject,
                program_id=program_id,
                source_revision=source_revision,
                candidate_revision=candidate_revision,
                request_kind="rebase",
                old_snapshot_id=old_snapshot["snapshot_id"],
                new_snapshot_id=new_snapshot["snapshot_id"],
                request_digest=materialized.request_digest,
                conflicts_digest=materialized.conflicts_digest,
                conflicts=list(materialized.conflicts),
            )
            return {"revision": revision, "conflict_report": report, "plan": None}
        if compilation is None:
            raise RepositoryConflict("compiler_result_invalid")
        plan, _ = await self.repository.seal_plan(
            owner_subject=owner_subject,
            program_id=program_id,
            revision=candidate_revision,
            compilation=compilation,
            rollout_policy_digest=self._rollout_policy_digest(),
            rollout_policy_epoch=self.flags.rollout_policy_epoch,
            plan_id=plan_id,
        )
        return {"revision": revision, "conflict_report": None, "plan": plan}

    async def bind_intent(
        self, *, owner_subject: str, intent_id: str, plan_id: str
    ) -> dict[str, Any]:
        plan = await self.repository.get_plan(owner_subject, plan_id)
        if plan is None:
            raise RepositoryConflict("not_found")
        if plan["invalidations"]:
            raise RepositoryConflict("sealed_plan_invalidated")
        if (
            plan["rollout_policy_digest"] != self._rollout_policy_digest()
            or plan["rollout_policy_epoch"] != self.flags.rollout_policy_epoch
        ):
            raise RepositoryConflict("policy_mismatch")
        digest = phase8_binding_digest(plan)
        binding, _ = await self.repository.bind_intent(
            owner_subject=owner_subject,
            intent_id=intent_id,
            plan_id=plan_id,
            binding_digest=digest,
        )
        await self.repository.append_usage_event(
            owner_subject=owner_subject,
            plan_id=plan_id,
            state="intent_created",
            external_id=intent_id,
            binding_digest=digest,
        )
        return binding

    async def bind_consent(
        self, *, owner_subject: str, consent_id: str, intent_id: str
    ) -> dict[str, Any]:
        binding, _ = await self.repository.bind_consent(
            owner_subject=owner_subject,
            consent_id=consent_id,
            intent_id=intent_id,
        )
        intent_binding = await self._intent_binding(owner_subject, intent_id)
        await self.repository.append_usage_event(
            owner_subject=owner_subject,
            plan_id=intent_binding["plan_id"],
            state="consent_created",
            external_id=consent_id,
            binding_digest=intent_binding["binding_digest"],
        )
        return binding

    async def admit(
        self,
        *,
        owner_subject: str,
        device_id: str,
        plan_id: str,
        action: str,
        cohort: str,
        reported_capabilities: tuple[str, ...],
        current_runtime_pins: dict[str, str],
    ) -> dict[str, Any]:
        if action not in {"preview", "commit"}:
            raise RepositoryConflict("admission_action_invalid")
        if not self.flags.source_enabled or not self.flags.compiler_enabled:
            raise RepositoryConflict("feature_disabled")
        plan = await self.repository.get_plan(owner_subject, plan_id)
        if plan is None:
            raise RepositoryConflict("not_found")
        if plan["invalidations"]:
            raise RepositoryConflict("sealed_plan_invalidated")
        if (
            plan["rollout_policy_digest"] != self._rollout_policy_digest()
            or plan["rollout_policy_epoch"] != self.flags.rollout_policy_epoch
        ):
            raise RepositoryConflict("policy_mismatch")
        if plan["modify_count"] or plan["erase_count"]:
            if (
                not self.flags.transform_pack_enabled
                or not self.flags.checkpoint_v2_enabled
                or plan["checkpoint_strategy"] != "cad.rollback.checkpoint/2"
            ):
                raise RepositoryConflict("capability_missing")
        elif plan["create_count"] and not self.flags.create_pack_enabled:
            raise RepositoryConflict("feature_disabled")
        if any(
            marker in pack.lower()
            for pack in plan["operation_packs"]
            for marker in _DESTRUCTIVE_PACK_MARKERS
        ):
            raise RepositoryConflict("capability_missing")
        if (
            current_runtime_pins.get("runtime_id") != "managed_dotnet"
            or current_runtime_pins.get("host_family") != "R25"
        ):
            raise RepositoryConflict("capability_missing")
        if any(
            current_runtime_pins.get(key) != expected
            for key, expected in plan["runtime_pins"].items()
        ):
            raise RepositoryConflict("binding_mismatch")
        packs = tuple(plan["operation_packs"])
        if not packs or any(pack not in self.allowed_packs for pack in packs):
            raise RepositoryConflict("capability_missing")
        reported = set(reported_capabilities)
        required = tuple(plan["required_capabilities"])
        if not set(required).issubset(reported):
            raise RepositoryConflict("capability_missing")
        support_states = (
            ("preview_only", "lab_commit", "certified")
            if action == "preview"
            else ("lab_commit", "certified")
        )
        evidence: list[dict[str, Any]] = []
        for capability in required:
            expected_pack = _capability_operation_pack(capability)
            match = None
            if expected_pack in packs:
                match = await self.repository.matching_capability_evidence(
                    owner_subject=owner_subject,
                    device_id=device_id,
                    capability_key=capability,
                    operation_pack=expected_pack,
                    runtime_id=current_runtime_pins["runtime_id"],
                    host_family=current_runtime_pins["host_family"],
                    cohort=cohort,
                    package_hash=current_runtime_pins["package_hash"],
                    capability_manifest_hash=current_runtime_pins[
                        "capability_manifest_hash"
                    ],
                    operation_registry_hash=current_runtime_pins[
                        "operation_registry_hash"
                    ],
                    minimum_support_states=support_states,
                )
            if match is None:
                raise RepositoryConflict("capability_missing")
            evidence.append(match)
        return {
            "plan_id": plan_id,
            "action": action,
            "binding_digest": phase8_binding_digest(plan),
            "capability_evidence_ids": [item["evidence_id"] for item in evidence],
            "capability_evidence": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"owner_subject", "created_at"}
                }
                for item in evidence
            ],
        }

    def _compile(
        self,
        source: dict[str, Any],
        *,
        materialized_target_refs: list[dict[str, Any]] | None = None,
        materialized_owner_id: str | None = None,
    ) -> CompiledProgram:
        if not self.flags.source_enabled or not self.flags.compiler_enabled:
            raise RepositoryConflict("feature_disabled")
        if self.compiler is None:
            raise RepositoryConflict("compiler_unavailable")
        result = self.compiler.compile(
            source,
            materialized_target_refs=materialized_target_refs,
            materialized_owner_id=materialized_owner_id,
        )
        if not isinstance(result, CompiledProgram):
            raise RepositoryConflict("compiler_result_invalid")
        return result

    @staticmethod
    def _require_source_binding(
        compilation: CompiledProgram,
        *,
        program_id: str,
        device_id: str,
        document_id: str,
        source_snapshot_id: str,
        expected_document_revision: str,
    ) -> None:
        expected = {
            "program_id": program_id,
            "program_revision": 1,
            "device_id": device_id,
            "document_id": document_id,
            "source_snapshot_id": source_snapshot_id,
            "expected_document_revision": expected_document_revision,
        }
        for field, value in expected.items():
            actual = compilation.source.get(field)
            # Legacy injected test adapters did not carry the canonical fields.
            # A real cad.program/1.0 source always does and must match exactly.
            if actual is not None and actual != value:
                raise RepositoryConflict("source_binding_mismatch")

    def _require_revision_adapter(self) -> None:
        if self.revision_adapter is None:
            raise RepositoryConflict("revision_adapter_unavailable")

    def _rollout_policy_digest(self) -> str:
        if self.flags.rollout_policy_epoch < 1:
            raise RepositoryConflict("rollout_policy_unconfigured")
        derived = canonical_rollout_policy_digest(self.flags)
        configured = self.flags.rollout_policy_digest
        if configured is not None and configured != derived:
            raise RepositoryConflict("rollout_policy_mismatch")
        return derived

    @staticmethod
    def _require_compiler_digests(
        materialized: Any, compilation: CompiledProgram
    ) -> None:
        if (
            compilation.source_digest != materialized.source_digest
            or compilation.semantic_digest != materialized.semantic_digest
        ):
            raise RepositoryConflict("compiler_source_mismatch")

    async def _intent_binding(
        self, owner_subject: str, intent_id: str
    ) -> dict[str, Any]:
        with self.repository.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_intent_bindings "
                "WHERE owner_subject = ? AND intent_id = ?",
                (owner_subject, intent_id),
            ).fetchone()
        if row is None:
            raise RepositoryConflict("not_found")
        return dict(row)
