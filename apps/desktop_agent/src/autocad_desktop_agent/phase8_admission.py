"""Fail-closed Desktop Agent admission for sealed Phase 8 execution plans."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from autocad_contracts.agent_protocol import canonical_json

from .config import AgentConfig
from .executor import AgentExecutionError

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSIONED_PACK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[1-9][0-9]{0,7}$")
_SUPPORT_RANK = {
    "unsupported": 0,
    "contract_only": 1,
    "preview_only": 2,
    "lab_commit": 3,
    "certified": 4,
}
_BUDGET_CEILINGS = {
    "operations": 256,
    "entities": 256,
    "vertices": 4096,
    "text_bytes": 65_536,
    "payload_bytes": 1_048_576,
    "result_bytes": 1_048_576,
    "checkpoint_bytes": 5_242_880,
}
_TOPOLOGY_OR_DESTRUCTIVE = {
    "delete",
    "erase",
    "trim",
    "extend",
    "fillet",
    "chamfer",
    "join",
    "explode",
}
_TRANSFORMS = {"move", "rotate", "scale"}
_FORBIDDEN_AUTHORITY_KEYS = {
    "command",
    "command_name",
    "code",
    "script",
    "shell",
    "lisp",
    "dll",
    "reflection",
    "path",
    "file",
    "url",
    "uri",
    "handle",
    "raw_handle",
    "object_id",
    "selection",
    "predicate",
}
_BINDING_PIN_FIELDS = (
    "runtime_id",
    "runtime_role",
    "host_family",
    "host_version",
    "package_id",
    "package_version",
    "package_hash",
    "capability_manifest_hash",
    "operation_registry_version",
    "operation_registry_hash",
    "policy_version",
)


@dataclass(frozen=True)
class Phase8AdmissionPolicy:
    source_enabled: bool
    create_pack_enabled: bool
    transform_pack_enabled: bool
    checkpoint_v2_enabled: bool
    operation_pack_allowlist: frozenset[str]
    rollout_policy_epoch: int

    @classmethod
    def from_config(cls, config: AgentConfig) -> "Phase8AdmissionPolicy":
        return cls(
            source_enabled=config.program_v1_source_enabled,
            create_pack_enabled=config.program_v1_create_pack_enabled,
            transform_pack_enabled=config.program_v1_transform_pack_enabled,
            checkpoint_v2_enabled=config.checkpoint_v2_enabled,
            operation_pack_allowlist=config.operation_pack_allowlist,
            rollout_policy_epoch=config.rollout_policy_epoch,
        )


@dataclass(frozen=True)
class VerifiedPhase8Plan:
    plan: dict[str, Any]
    approval_binding: dict[str, Any] | None
    execution_plan_digest: str
    effect_manifest_digest: str
    target_set_digest: str
    hard_budget_digest: str
    rollout_policy_digest: str
    effect_identity_digest: str | None
    required_capabilities: tuple[str, ...]
    operation_packs: tuple[str, ...]
    modifies_entities: bool
    hard_result_bytes: int

    def host_arguments(self) -> dict[str, Any]:
        """Return only sealed artifacts. Source programs are never forwarded."""

        arguments: dict[str, Any] = {"execution_plan": self.plan}
        if self.approval_binding is not None:
            arguments["approval_binding"] = self.approval_binding
        return arguments


class Phase8PlanAdmission:
    """Verify exact sealed data without evaluating CAD Program source semantics."""

    def __init__(self, policy: Phase8AdmissionPolicy) -> None:
        self._policy = policy

    def verify(
        self,
        plan: Any,
        *,
        binding: Any,
        command_kind: str,
        approval_binding: Any | None,
        capability_states: Mapping[str, str],
        server_capability_evidence: Any,
        device_id: str | None = None,
        preview_id: str | None = None,
        receipt_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> VerifiedPhase8Plan:
        value = _as_mapping(plan, "plan_mismatch")
        bound = _as_mapping(binding, "binding_mismatch")
        if not self._policy.source_enabled:
            raise AgentExecutionError("feature_disabled")
        if value.get("schema_version") != "cad.execution-plan/1":
            raise AgentExecutionError("plan_mismatch")

        source = _required_mapping(value, "source", "source_mismatch")
        compiler = _required_mapping(value, "compiler", "compiler_mismatch")
        operations = _required_list(value, "operations")
        effect_manifest = _required_mapping(value, "effect_manifest", "effect_mismatch")
        pins = _required_mapping(value, "pins", "binding_mismatch")
        budgets = _required_mapping(value, "budgets", "budget_mismatch")
        required_capabilities = _required_list(value, "required_capabilities")
        operation_packs = _required_list(value, "operation_packs")
        materialized_refs = _required_list(value, "materialized_refs")
        validation_profiles = _required_list(value, "validation_profiles")
        checkpoint_strategy = _required_mapping(
            value,
            "checkpoint_strategy",
            "checkpoint_mismatch",
        )
        rollout_policy = _required_mapping(
            value,
            "rollout_policy",
            "rollout_policy_mismatch",
        )

        if (
            source.get("schema_version") != "cad.program/1.0"
            or source.get("digest_domain") != "cad.program.source/1"
        ):
            raise AgentExecutionError("source_mismatch")
        if (
            source.get("document_id") != bound.get("document_id")
            or str(source.get("document_revision"))
            != str(bound.get("document_revision"))
            or not isinstance(source.get("snapshot_id"), str)
            or not source["snapshot_id"]
            or (
                device_id is not None
                and source.get("device_id") != device_id
            )
        ):
            raise AgentExecutionError("binding_mismatch")
        if _contains_forbidden_authority(value):
            raise AgentExecutionError("capability_missing")
        source_digest = _required_digest(source, "digest", "source_mismatch")
        compiler_hash = _required_digest(compiler, "hash", "compiler_mismatch")
        if any(
            not isinstance(compiler.get(field), str) or not compiler[field]
            for field in ("id", "version")
        ):
            raise AgentExecutionError("compiler_mismatch")
        expansion_digest = _required_digest(
            value, "expansion_digest", "expansion_mismatch"
        )
        effect_digest = _required_digest(
            value, "effect_manifest_digest", "effect_mismatch"
        )
        plan_digest = _required_digest(
            value, "execution_plan_digest", "plan_mismatch"
        )

        if bound.get("program_digest") != source_digest:
            raise AgentExecutionError("source_mismatch")
        if bound.get("execution_digest") != plan_digest:
            raise AgentExecutionError("plan_mismatch")
        if expansion_digest != _canonical_digest(
            "cad.program.expansion/1",
            {"operations": operations},
        ):
            raise AgentExecutionError("expansion_mismatch")
        if effect_digest != _canonical_digest(
            "cad.effect-manifest/1",
            effect_manifest,
        ):
            raise AgentExecutionError("effect_mismatch")
        target_set_digest = _required_digest(
            value,
            "target_set_digest",
            "target_mismatch",
        )
        if target_set_digest != _canonical_digest(
            "cad.target-refs/1",
            {"target_refs": materialized_refs},
        ):
            raise AgentExecutionError("target_mismatch")
        validation_profile_digest = _required_digest(
            value,
            "validation_profile_digest",
            "validation_mismatch",
        )
        if validation_profile_digest != _canonical_digest(
            "cad.validation-profiles/1",
            {"validation_profiles": validation_profiles},
        ):
            raise AgentExecutionError("validation_mismatch")
        checkpoint_strategy_digest = _required_digest(
            value,
            "checkpoint_strategy_digest",
            "checkpoint_mismatch",
        )
        if checkpoint_strategy_digest != _canonical_digest(
            "cad.checkpoint-strategy/1",
            checkpoint_strategy,
        ):
            raise AgentExecutionError("checkpoint_mismatch")
        hard_budget_digest = _required_digest(
            value,
            "hard_budget_digest",
            "budget_mismatch",
        )
        hard_budgets = _required_mapping(budgets, "hard", "budget_mismatch")
        if hard_budget_digest != _canonical_digest(
            "cad.execution-budgets/1",
            {
                "max_operations": hard_budgets["operations"],
                "max_entities": hard_budgets["entities"],
                "max_vertices": hard_budgets["vertices"],
                "max_text_bytes": hard_budgets["text_bytes"],
            },
        ):
            raise AgentExecutionError("budget_mismatch")
        digest_payload = dict(value)
        digest_payload.pop("execution_plan_digest", None)
        if plan_digest != _canonical_digest(
            "cad.execution-plan/1",
            digest_payload,
        ):
            raise AgentExecutionError("plan_mismatch")

        for field in _BINDING_PIN_FIELDS:
            if (
                field not in pins
                or field not in bound
                or pins[field] in {None, ""}
                or pins[field] != bound[field]
            ):
                raise AgentExecutionError(_pin_error(field))
        if pins.get("runtime_id") != "managed_dotnet":
            raise AgentExecutionError("runtime_mismatch")
        if pins.get("runtime_role") != "primary" or pins.get("host_family") != "R25":
            raise AgentExecutionError("runtime_mismatch")

        effects = self._verify_effects(
            operations,
            effect_manifest,
            operation_packs,
            required_capabilities,
        )
        self._verify_materialized_refs(
            operations,
            materialized_refs,
            source=source,
        )
        hard_result_bytes = self._verify_budgets(value, operations, effects, budgets)
        evidence, server_states = self._verify_server_evidence(
            server_capability_evidence,
            pins=pins,
            compiler_hash=compiler_hash,
        )
        self._verify_rollout_policy(
            rollout_policy,
            pins=pins,
            capability_evidence_digest=evidence["digest"],
            operation_packs=operation_packs,
        )
        effective_states = {
            key: min(
                (capability_states.get(key), server_states.get(key)),
                key=lambda state: _SUPPORT_RANK.get(str(state), -1),
            )
            for key in set(capability_states).intersection(server_states)
        }
        capabilities = self._verify_capabilities(
            required_capabilities,
            effective_states,
            command_kind=command_kind,
        )
        capability_state_hash = _canonical_digest(
            "cad.capability-intersection/1",
            {
                "capability_states": {
                    key: effective_states[key]
                    for key in sorted(capabilities)
                }
            }
        )
        if pins.get("capability_state_hash") != capability_state_hash:
            raise AgentExecutionError("capability_mismatch")

        approval = (
            None
            if approval_binding is None
            else _as_mapping(approval_binding, "approval_binding_mismatch")
        )
        if command_kind == "program_commit":
            self._verify_approval(
                approval,
                source_digest=source_digest,
                compiler_hash=compiler_hash,
                expansion_digest=expansion_digest,
                effect_digest=effect_digest,
                plan_digest=plan_digest,
                pins=pins,
                capability_state_hash=capability_state_hash,
                target_set_digest=target_set_digest,
                validation_profile_digest=validation_profile_digest,
                checkpoint_strategy_digest=checkpoint_strategy_digest,
                hard_budget_digest=hard_budget_digest,
                command_kind=command_kind,
                preview_id=preview_id,
                receipt_id=receipt_id,
            )
        elif approval is not None:
            raise AgentExecutionError("approval_binding_mismatch")

        modifies = effects["modify_count"] > 0
        checkpoint_schema = checkpoint_strategy.get("schema_version")
        if effects["erase_count"] > 0:
            raise AgentExecutionError("capability_missing")
        if modifies:
            if (
                not self._policy.transform_pack_enabled
                or not self._policy.checkpoint_v2_enabled
                or checkpoint_schema != "cad.rollback.checkpoint/2"
            ):
                raise AgentExecutionError("checkpoint_mismatch")
        elif effects["create_count"] > 0 and not self._policy.create_pack_enabled:
            raise AgentExecutionError("capability_missing")
        if checkpoint_schema == "cad.rollback.checkpoint/1" and modifies:
            raise AgentExecutionError("checkpoint_mismatch")

        effect_identity_digest = None
        if command_kind == "program_commit":
            effect_identity_digest = self._verify_effect_identity(
                binding=bound,
                plan_digest=plan_digest,
                effect_digest=effect_digest,
                target_set_digest=target_set_digest,
                checkpoint_strategy_digest=checkpoint_strategy_digest,
                operation_packs=operation_packs,
                preview_id=preview_id,
                receipt_id=receipt_id,
                preview_expires_at=approval.get("preview_expires_at"),
                rollout_policy_digest=pins.get("rollout_policy_digest"),
            )
            if approval is None or approval.get(
                "effect_identity_digest"
            ) != effect_identity_digest:
                raise AgentExecutionError("approval_binding_mismatch")
            if idempotency_key != effect_identity_digest:
                raise AgentExecutionError("effect_identity_mismatch")

        return VerifiedPhase8Plan(
            plan=value,
            approval_binding=approval,
            execution_plan_digest=plan_digest,
            effect_manifest_digest=effect_digest,
            target_set_digest=target_set_digest,
            hard_budget_digest=hard_budget_digest,
            rollout_policy_digest=pins["rollout_policy_digest"],
            effect_identity_digest=effect_identity_digest,
            required_capabilities=capabilities,
            operation_packs=tuple(str(item) for item in operation_packs),
            modifies_entities=modifies,
            hard_result_bytes=hard_result_bytes,
        )

    def verify_result(
        self,
        admission: VerifiedPhase8Plan,
        result: Any,
        *,
        command_kind: str,
    ) -> dict[str, Any]:
        value = _as_mapping(result, "host_result_mismatch")
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > admission.hard_result_bytes:
            raise AgentExecutionError("budget_exceeded")
        if value.get("execution_plan_digest") != admission.execution_plan_digest:
            raise AgentExecutionError("plan_mismatch")
        if value.get("effect_manifest_digest") != admission.effect_manifest_digest:
            raise AgentExecutionError("effect_mismatch")
        if value.get("target_set_digest") != admission.target_set_digest:
            raise AgentExecutionError("target_mismatch")
        if value.get("hard_budget_digest") != admission.hard_budget_digest:
            raise AgentExecutionError("budget_mismatch")
        if value.get("rollout_policy_digest") != admission.rollout_policy_digest:
            raise AgentExecutionError("rollout_policy_mismatch")
        if command_kind == "program_preview":
            if (
                value.get("transaction_aborted") is not True
                or value.get("drawing_unchanged") is not True
            ):
                raise AgentExecutionError("preview_mismatch")
        if command_kind == "program_commit" and admission.modifies_entities:
            checkpoint = _required_mapping(
                value,
                "checkpoint",
                "checkpoint_mismatch",
            )
            if checkpoint.get("schema_version") != "cad.rollback.checkpoint/2":
                raise AgentExecutionError("checkpoint_mismatch")
            _required_digest(checkpoint, "digest", "checkpoint_mismatch")
            if value.get("milestone") != "effect_and_receipt_committed":
                raise AgentExecutionError("outcome_unknown")
        if (
            command_kind == "program_commit"
            and value.get("effect_identity_digest")
            != admission.effect_identity_digest
        ):
            raise AgentExecutionError("effect_identity_mismatch")
        return value

    def _verify_effects(
        self,
        operations: list[Any],
        manifest: dict[str, Any],
        operation_packs: list[Any],
        required_capabilities: list[Any],
    ) -> dict[str, int]:
        manifest_operations = _required_list(manifest, "operations")
        totals = _required_mapping(manifest, "totals", "effect_mismatch")
        if len(manifest_operations) != len(operations):
            raise AgentExecutionError("effect_mismatch")

        plan_ids = []
        effect_ids = []
        requirement_keys = {
            str(_as_mapping(item, "capability_missing").get("key"))
            for item in required_capabilities
        }
        packs = tuple(str(item) for item in operation_packs)
        calculated = {"create_count": 0, "modify_count": 0, "erase_count": 0}
        for operation, effect in zip(operations, manifest_operations, strict=True):
            operation_value = _as_mapping(operation, "plan_mismatch")
            effect_value = _as_mapping(effect, "effect_mismatch")
            operation_id = operation_value.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id:
                raise AgentExecutionError("plan_mismatch")
            plan_ids.append(operation_id)
            effect_ids.append(effect_value.get("operation_id"))
            if (
                effect_value.get("capability_key") not in requirement_keys
                or effect_value.get("operation_pack") not in packs
            ):
                raise AgentExecutionError("effect_mismatch")
            kind = str(operation_value.get("kind", ""))
            operation_tokens = set(kind.split("."))
            if _TOPOLOGY_OR_DESTRUCTIVE.intersection(operation_tokens):
                raise AgentExecutionError("capability_missing")
            for field in calculated:
                count = effect_value.get(field)
                if type(count) is not int or count < 0:
                    raise AgentExecutionError("effect_mismatch")
                calculated[field] += count
            if (
                effect_value.get("modify_count", 0) > 0
                and not _TRANSFORMS.intersection(operation_tokens)
            ):
                raise AgentExecutionError("capability_missing")
        if plan_ids != effect_ids or len(plan_ids) != len(set(plan_ids)):
            raise AgentExecutionError("effect_mismatch")
        if any(totals.get(field) != count for field, count in calculated.items()):
            raise AgentExecutionError("effect_mismatch")

        if (
            not packs
            or len(packs) != len(set(packs))
            or any(_VERSIONED_PACK.fullmatch(pack) is None for pack in packs)
            or any(pack not in self._policy.operation_pack_allowlist for pack in packs)
        ):
            raise AgentExecutionError("capability_missing")
        return calculated

    @staticmethod
    def _verify_materialized_refs(
        operations: list[Any],
        materialized_refs: list[Any],
        *,
        source: dict[str, Any],
    ) -> None:
        refs: dict[str, dict[str, Any]] = {}
        for item in materialized_refs:
            ref = _as_mapping(item, "reference_mismatch")
            ref_id = ref.get("ref_id")
            if (
                not isinstance(ref_id, str)
                or not ref_id
                or ref_id in refs
                or ref.get("device_id") != source.get("device_id")
                or ref.get("document_id") != source.get("document_id")
                or ref.get("snapshot_id") != source.get("snapshot_id")
                or str(ref.get("document_revision"))
                != str(source.get("document_revision"))
                or not isinstance(ref.get("entity_id"), str)
                or not ref["entity_id"]
                or not isinstance(ref.get("entity_type"), str)
                or not ref["entity_type"]
            ):
                raise AgentExecutionError("reference_mismatch")
            _required_digest(ref, "fingerprint", "reference_mismatch")
            refs[ref_id] = ref
        for operation in operations:
            arguments = _as_mapping(
                _as_mapping(operation, "plan_mismatch").get("arguments"),
                "plan_mismatch",
            )
            target_ref = arguments.get("target_ref")
            if target_ref is not None and target_ref not in refs:
                raise AgentExecutionError("reference_mismatch")

    @staticmethod
    def _verify_budgets(
        plan: dict[str, Any],
        operations: list[Any],
        effects: dict[str, int],
        budgets: dict[str, Any],
    ) -> int:
        estimated = _required_mapping(budgets, "estimated", "budget_mismatch")
        hard = _required_mapping(budgets, "hard", "budget_mismatch")
        actual_minimums = {
            "operations": len(operations),
            "entities": sum(effects.values()),
        }
        for field, ceiling in _BUDGET_CEILINGS.items():
            hard_value = hard.get(field)
            estimate = estimated.get(field)
            if (
                type(hard_value) is not int
                or type(estimate) is not int
                or hard_value < 0
                or hard_value > ceiling
                or estimate < 0
                or estimate > hard_value
            ):
                raise AgentExecutionError("budget_exceeded")
            if estimate < actual_minimums.get(field, 0):
                raise AgentExecutionError("budget_mismatch")
        payload_size = len(
            json.dumps(
                plan,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if payload_size > hard["payload_bytes"]:
            raise AgentExecutionError("budget_exceeded")
        return hard["result_bytes"]

    @staticmethod
    def _verify_capabilities(
        requirements: list[Any],
        capability_states: Mapping[str, str],
        *,
        command_kind: str,
    ) -> tuple[str, ...]:
        minimum_floor = "lab_commit" if command_kind == "program_commit" else "preview_only"
        keys: list[str] = []
        for requirement in requirements:
            value = _as_mapping(requirement, "capability_missing")
            key = value.get("key")
            minimum_state = value.get("minimum_state")
            current_state = capability_states.get(str(key))
            if (
                not isinstance(key, str)
                or not key
                or minimum_state not in _SUPPORT_RANK
                or current_state not in _SUPPORT_RANK
                or _SUPPORT_RANK[current_state] < _SUPPORT_RANK[minimum_state]
                or _SUPPORT_RANK[current_state] < _SUPPORT_RANK[minimum_floor]
            ):
                raise AgentExecutionError("capability_missing")
            keys.append(key)
        if not keys or len(keys) != len(set(keys)):
            raise AgentExecutionError("capability_missing")
        return tuple(keys)

    @staticmethod
    def _verify_approval(
        approval: dict[str, Any] | None,
        *,
        source_digest: str,
        compiler_hash: str,
        expansion_digest: str,
        effect_digest: str,
        plan_digest: str,
        pins: dict[str, Any],
        capability_state_hash: str,
        target_set_digest: str,
        validation_profile_digest: str,
        checkpoint_strategy_digest: str,
        hard_budget_digest: str,
        command_kind: str,
        preview_id: str | None,
        receipt_id: str | None,
    ) -> None:
        if approval is None:
            raise AgentExecutionError("approval_required")
        if approval.get("binding_domain") != "cad.execution-intent/2":
            raise AgentExecutionError("approval_binding_mismatch")
        expected = {
            "source_digest": source_digest,
            "compiler_hash": compiler_hash,
            "expansion_digest": expansion_digest,
            "effect_manifest_digest": effect_digest,
            "execution_plan_digest": plan_digest,
            "capability_state_hash": capability_state_hash,
            "target_set_digest": target_set_digest,
            "validation_profile_digest": validation_profile_digest,
            "checkpoint_strategy_digest": checkpoint_strategy_digest,
            "hard_budget_digest": hard_budget_digest,
            "action": command_kind,
            "preview_id": preview_id,
            "receipt_id": receipt_id,
            **{field: pins[field] for field in _BINDING_PIN_FIELDS},
        }
        if any(approval.get(field) != value for field, value in expected.items()):
            raise AgentExecutionError("approval_binding_mismatch")
        for field in ("intent_digest", "approval_proof_digest"):
            _required_digest(approval, field, "approval_binding_mismatch")
        consent_id = approval.get("consent_id")
        if not isinstance(consent_id, str) or not consent_id:
            raise AgentExecutionError("approval_binding_mismatch")
        if _timestamp(
            approval.get("preview_expires_at"),
            "approval_binding_mismatch",
        ) <= datetime.now(timezone.utc):
            raise AgentExecutionError("approval_binding_mismatch")

    def _verify_server_evidence(
        self,
        evidence: Any,
        *,
        pins: dict[str, Any],
        compiler_hash: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        value = _as_mapping(evidence, "capability_missing")
        if value.get("schema_version") != "cad.capability-evidence/1":
            raise AgentExecutionError("capability_missing")
        digest = _required_digest(value, "digest", "capability_missing")
        payload = dict(value)
        payload.pop("digest", None)
        if digest != _canonical_digest("cad.capability-evidence/1", payload):
            raise AgentExecutionError("capability_missing")
        expires_at = _timestamp(value.get("expires_at"), "capability_missing")
        if (
            value.get("revoked") is not False
            or expires_at <= datetime.now(timezone.utc)
            or value.get("rollout_policy_epoch")
            != self._policy.rollout_policy_epoch
            or value.get("package_hash") != pins.get("package_hash")
            or value.get("host_family") != pins.get("host_family")
            or value.get("operation_registry_hash")
            != pins.get("operation_registry_hash")
            or value.get("compiler_hash") != compiler_hash
            or value.get("policy_version") != pins.get("policy_version")
        ):
            raise AgentExecutionError("capability_missing")
        states: dict[str, str] = {}
        for item in _required_list(value, "capabilities"):
            claim = _as_mapping(item, "capability_missing")
            key = claim.get("key")
            state = claim.get("state")
            if (
                not isinstance(key, str)
                or not key
                or key in states
                or state not in _SUPPORT_RANK
            ):
                raise AgentExecutionError("capability_missing")
            states[key] = state
        if not states:
            raise AgentExecutionError("capability_missing")
        if pins.get("capability_evidence_digest") != digest:
            raise AgentExecutionError("capability_mismatch")
        return value, states

    def _verify_rollout_policy(
        self,
        rollout: dict[str, Any],
        *,
        pins: dict[str, Any],
        capability_evidence_digest: str,
        operation_packs: list[Any],
    ) -> None:
        digest = _required_digest(
            rollout,
            "digest",
            "rollout_policy_mismatch",
        )
        payload = dict(rollout)
        payload.pop("digest", None)
        if (
            digest != _canonical_digest("cad.rollout-policy/1", payload)
            or pins.get("rollout_policy_digest") != digest
            or rollout.get("epoch") != self._policy.rollout_policy_epoch
            or rollout.get("source_enabled") is not self._policy.source_enabled
            or rollout.get("create_pack_enabled")
            is not self._policy.create_pack_enabled
            or rollout.get("transform_pack_enabled")
            is not self._policy.transform_pack_enabled
            or rollout.get("checkpoint_v2_enabled")
            is not self._policy.checkpoint_v2_enabled
            or rollout.get("topology_pack_enabled") is not False
            or rollout.get("delete_pack_enabled") is not False
            or rollout.get("lt_write_enabled") is not False
            or rollout.get("operation_pack_allowlist")
            != sorted(self._policy.operation_pack_allowlist)
            or any(
                pack not in rollout.get("operation_pack_allowlist", ())
                for pack in operation_packs
            )
            or rollout.get("capability_evidence_digest")
            != capability_evidence_digest
            or rollout.get("cohort_allowed") is not True
            or not isinstance(rollout.get("cohort_id"), str)
            or not rollout["cohort_id"]
            or rollout.get("runtime_allowlist") != ["managed_dotnet:R25"]
            or not isinstance(rollout.get("entity_type_allowlist"), list)
            or not rollout["entity_type_allowlist"]
        ):
            raise AgentExecutionError("rollout_policy_mismatch")

    @staticmethod
    def _verify_effect_identity(
        *,
        binding: dict[str, Any],
        plan_digest: str,
        effect_digest: str,
        target_set_digest: str,
        checkpoint_strategy_digest: str,
        operation_packs: list[Any],
        preview_id: str | None,
        receipt_id: str | None,
        preview_expires_at: Any,
        rollout_policy_digest: Any,
    ) -> str:
        if not isinstance(preview_id, str) or not preview_id:
            raise AgentExecutionError("effect_identity_mismatch")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise AgentExecutionError("effect_identity_mismatch")
        identity = {
            "action": "program_commit",
            "execution_plan_digest": plan_digest,
            "effect_manifest_digest": effect_digest,
            "target_set_digest": target_set_digest,
            "document_id": binding.get("document_id"),
            "document_revision_before": binding.get("document_revision"),
            "preview_id": preview_id,
            "preview_expires_at": preview_expires_at,
            "receipt_id": receipt_id,
            "checkpoint_strategy_digest": checkpoint_strategy_digest,
            "operation_packs": operation_packs,
            "runtime_id": binding.get("runtime_id"),
            "host_family": binding.get("host_family"),
            "package_hash": binding.get("package_hash"),
            "operation_registry_hash": binding.get("operation_registry_hash"),
            "policy_version": binding.get("policy_version"),
            "rollout_policy_digest": rollout_policy_digest,
        }
        return _canonical_digest("cad.effect-identity/1", identity)


def _as_mapping(value: Any, code: str) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    if not isinstance(value, Mapping):
        raise AgentExecutionError(code)
    return dict(value)


def _required_mapping(
    value: Mapping[str, Any],
    field: str,
    code: str,
) -> dict[str, Any]:
    return _as_mapping(value.get(field), code)


def _required_list(value: Mapping[str, Any], field: str) -> list[Any]:
    result = value.get(field)
    if not isinstance(result, list):
        raise AgentExecutionError("plan_mismatch")
    return result


def _required_digest(value: Mapping[str, Any], field: str, code: str) -> str:
    digest = value.get(field)
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise AgentExecutionError(code)
    return digest


def _canonical_digest(domain: str, value: dict[str, Any]) -> str:
    encoded = canonical_json(
        {
            "domain": domain,
            "payload": value,
        }
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise AgentExecutionError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AgentExecutionError(code) from error
    if parsed.tzinfo is None:
        raise AgentExecutionError(code)
    return parsed.astimezone(timezone.utc)


def _contains_forbidden_authority(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_AUTHORITY_KEYS:
                return True
            if _contains_forbidden_authority(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_authority(item) for item in value)
    return False


def _pin_error(field: str) -> str:
    if field.startswith("runtime") or field.startswith("host"):
        return "runtime_mismatch"
    if field.startswith("package"):
        return "package_mismatch"
    if field.startswith("capability"):
        return "capability_mismatch"
    if field.startswith("operation_registry"):
        return "registry_mismatch"
    return "policy_mismatch"
