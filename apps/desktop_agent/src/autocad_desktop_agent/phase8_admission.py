"""Fail-closed Desktop admission for canonical Phase 8 wire artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence

from autocad_contracts import (
    CadExecutionPlanV1,
    ExecutionBindingV1,
    Phase8ApprovalBinding,
    Phase8CapabilityEvidence,
    canonical_phase8_capability_evidence_digest,
    parse_execution_binding_v1,
    parse_execution_plan_v1,
    verify_execution_binding_v1,
)
from autocad_contracts.agent_protocol import canonical_json
from pydantic import ValidationError

from .config import AgentConfig
from .executor import AgentExecutionError

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SUPPORT_RANK = {
    "unsupported": 0,
    "contract_only": 1,
    "preview_only": 2,
    "lab_commit": 3,
    "certified": 4,
}
_BUDGET_CEILINGS = {
    "hard_max_operations": 256,
    "hard_max_entities": 256,
    "hard_max_vertices": 4096,
    "hard_max_text_bytes": 65_536,
}
_MAX_PLAN_BYTES = 1_048_576
_MAX_RESULT_BYTES = 1_048_576
_FORBIDDEN_PACK_MARKERS = (
    "delete",
    "erase",
    "trim",
    "extend",
    "fillet",
    "chamfer",
    "topology",
    "shell",
    "script",
    "lisp",
)
_PIN_FIELDS = (
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
    "rollout_policy_digest",
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
    capability_evidence: tuple[dict[str, Any], ...]
    execution_plan_digest: str
    effect_manifest_digest: str
    target_refs_digest: str
    hard_budgets_digest: str
    rollout_policy_digest: str
    effect_identity_digest: str | None
    required_capabilities: tuple[str, ...]
    operation_packs: tuple[str, ...]
    modifies_entities: bool
    hard_result_bytes: int = _MAX_RESULT_BYTES

    def host_arguments(self) -> dict[str, Any]:
        """Forward only sealed execution authority, never CAD Program source."""

        result: dict[str, Any] = {
            "execution_plan": self.plan,
            "capability_evidence": list(self.capability_evidence),
        }
        if self.approval_binding is not None:
            result["approval_binding"] = self.approval_binding
        return result


class Phase8PlanAdmission:
    """Use the shared contract verifier, then enforce local runtime policy."""

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
        legacy_binding: Any | None = None,
        device_id: str | None = None,
        job_id: str | None = None,
        command_id: str | None = None,
        issued_at: str | None = None,
        preview_id: str | None = None,
        preview_digest: str | None = None,
        preview_expires_at: str | None = None,
        receipt_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> VerifiedPhase8Plan:
        if not self._policy.source_enabled:
            raise AgentExecutionError("feature_disabled")
        if command_kind not in {"program_preview", "program_commit"}:
            raise AgentExecutionError("capability_missing")

        parsed_plan = _parse_plan(plan)
        parsed_binding = _parse_binding(binding)
        action = "preview" if command_kind == "program_preview" else "commit"
        expected_expiry = (
            preview_expires_at
            if preview_expires_at is not None
            else parsed_binding.preview_expires_at
        )
        try:
            verify_execution_binding_v1(
                parsed_binding,
                parsed_plan,
                expected_action=action,
                expected_preview_id=preview_id,
                expected_preview_expires_at=expected_expiry,
                expected_receipt_id=receipt_id if action == "commit" else None,
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise AgentExecutionError("binding_mismatch") from error

        if device_id is not None and (
            parsed_plan.device_id != device_id
            or parsed_binding.device_id != device_id
        ):
            raise AgentExecutionError("binding_mismatch")
        self._verify_legacy_binding(legacy_binding, parsed_plan, parsed_binding)
        self._verify_runtime_pins(parsed_plan, parsed_binding)
        plan_value = parsed_plan.model_dump(mode="json", exclude_none=True)
        self._verify_budgets(parsed_plan, plan_value)
        entity_types = self._verify_effect_boundary(parsed_plan)

        evidence, server_states, operation_packs = self._verify_evidence(
            server_capability_evidence,
            plan=parsed_plan,
            entity_types=entity_types,
            issued_at=issued_at,
        )
        effective_states = self._effective_capabilities(
            parsed_plan.required_capabilities,
            capability_states,
            server_states,
            command_kind=command_kind,
        )
        capability_state_hash = _canonical_digest(
            "cad.capability-intersection/1",
            {
                "capability_states": {
                    key: effective_states[key] for key in sorted(effective_states)
                }
            },
        )

        approval: Phase8ApprovalBinding | None = None
        effect_identity: str | None = None
        if action == "preview":
            if approval_binding is not None:
                raise AgentExecutionError("approval_binding_mismatch")
        else:
            approval = _parse_approval(approval_binding)
            self._verify_approval(
                approval,
                plan=parsed_plan,
                binding=parsed_binding,
                device_id=device_id,
                job_id=job_id,
                command_id=command_id,
                idempotency_key=idempotency_key,
                preview_id=preview_id,
                preview_digest=preview_digest,
                preview_expires_at=expected_expiry,
                receipt_id=receipt_id,
            )
            effect_identity = self._effect_identity(
                parsed_plan,
                parsed_binding,
                approval,
                evidence=evidence,
                operation_packs=operation_packs,
                capability_state_hash=capability_state_hash,
            )

        return VerifiedPhase8Plan(
            plan=plan_value,
            approval_binding=(
                None if approval is None else approval.model_dump(mode="json")
            ),
            capability_evidence=tuple(
                item.model_dump(mode="json", exclude_none=True)
                for item in evidence
            ),
            execution_plan_digest=parsed_plan.execution_plan_digest,
            effect_manifest_digest=parsed_plan.effect_manifest_digest,
            target_refs_digest=parsed_plan.target_refs_digest,
            hard_budgets_digest=parsed_plan.hard_budgets_digest,
            rollout_policy_digest=parsed_plan.execution_pins.rollout_policy_digest,
            effect_identity_digest=effect_identity,
            required_capabilities=tuple(parsed_plan.required_capabilities),
            operation_packs=operation_packs,
            modifies_entities=parsed_plan.effect_manifest.modifies > 0,
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
        expected = {
            "execution_plan_digest": admission.execution_plan_digest,
            "effect_manifest_digest": admission.effect_manifest_digest,
            "target_refs_digest": admission.target_refs_digest,
            "hard_budgets_digest": admission.hard_budgets_digest,
            "rollout_policy_digest": admission.rollout_policy_digest,
        }
        errors = {
            "execution_plan_digest": "plan_mismatch",
            "effect_manifest_digest": "effect_mismatch",
            "target_refs_digest": "target_mismatch",
            "hard_budgets_digest": "budget_mismatch",
            "rollout_policy_digest": "rollout_policy_mismatch",
        }
        for field, exact in expected.items():
            if value.get(field) != exact:
                raise AgentExecutionError(errors[field])
        if command_kind == "program_preview":
            if (
                value.get("transaction_aborted") is not True
                or value.get("drawing_unchanged") is not True
            ):
                raise AgentExecutionError("preview_mismatch")
        elif command_kind == "program_commit":
            if value.get("effect_identity_digest") != admission.effect_identity_digest:
                raise AgentExecutionError("effect_identity_mismatch")
            if admission.modifies_entities:
                checkpoint = _as_mapping(
                    value.get("checkpoint"),
                    "checkpoint_mismatch",
                )
                if checkpoint.get("schema_version") != "cad.rollback.checkpoint/2":
                    raise AgentExecutionError("checkpoint_mismatch")
                digest = checkpoint.get("digest")
                if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                    raise AgentExecutionError("checkpoint_mismatch")
            if value.get("milestone") != "effect_and_receipt_committed":
                raise AgentExecutionError("outcome_unknown")
        else:
            raise AgentExecutionError("capability_missing")
        return value

    @staticmethod
    def _verify_runtime_pins(
        plan: CadExecutionPlanV1,
        binding: ExecutionBindingV1,
    ) -> None:
        pins = plan.execution_pins
        if (
            pins.runtime_id != "managed_dotnet"
            or pins.runtime_role != "primary"
            or pins.host_family != "R25"
        ):
            raise AgentExecutionError("runtime_mismatch")
        for field in _PIN_FIELDS:
            if getattr(pins, field) != getattr(binding, field):
                raise AgentExecutionError(_pin_error(field))

    @staticmethod
    def _verify_legacy_binding(
        legacy_binding: Any | None,
        plan: CadExecutionPlanV1,
        binding: ExecutionBindingV1,
    ) -> None:
        if legacy_binding is None:
            return
        legacy = _as_mapping(legacy_binding, "binding_mismatch")
        expected = {
            "program_digest": plan.source_digest,
            "execution_digest": plan.execution_plan_digest,
            "document_id": plan.document_id,
            "document_revision": plan.expected_document_revision,
            **{
                field: getattr(binding, field)
                for field in _PIN_FIELDS
                if field != "rollout_policy_digest"
            },
        }
        for field, exact in expected.items():
            if legacy.get(field) != exact:
                raise AgentExecutionError(_pin_error(field))

    @staticmethod
    def _verify_budgets(
        plan: CadExecutionPlanV1,
        value: dict[str, Any],
    ) -> None:
        for field, ceiling in _BUDGET_CEILINGS.items():
            if getattr(plan.budgets, field) > ceiling:
                raise AgentExecutionError("budget_exceeded")
        if len(canonical_json(value).encode("utf-8")) > _MAX_PLAN_BYTES:
            raise AgentExecutionError("budget_exceeded")

    def _verify_effect_boundary(self, plan: CadExecutionPlanV1) -> set[str]:
        manifest = plan.effect_manifest
        if manifest.erases:
            raise AgentExecutionError("capability_missing")
        if manifest.creates and not self._policy.create_pack_enabled:
            raise AgentExecutionError("capability_missing")
        if manifest.modifies:
            if (
                not self._policy.transform_pack_enabled
                or not self._policy.checkpoint_v2_enabled
                or plan.checkpoint_strategy != "cad.rollback.checkpoint/2"
            ):
                raise AgentExecutionError("checkpoint_mismatch")
            refs = {
                item.ref_id: item for item in plan.materialized_target_refs
            }
            for operation, effect in zip(
                plan.operations,
                manifest.entries,
                strict=True,
            ):
                if not effect.modifies:
                    continue
                if (
                    operation.kind != "move_entity"
                    or operation.target_ref_id is None
                    or operation.target_ref_id not in refs
                ):
                    raise AgentExecutionError("capability_missing")
                ref = refs[operation.target_ref_id]
                capability = f"cad.op.move.{ref.entity_type.lower()}.v1"
                if (
                    ref.entity_type not in {"LINE", "CIRCLE", "LWPOLYLINE"}
                    or effect.entity_type != ref.entity_type
                    or capability not in plan.required_capabilities
                ):
                    raise AgentExecutionError("capability_missing")
        elif plan.checkpoint_strategy != "cad.rollback.checkpoint/1-created-entities":
            raise AgentExecutionError("checkpoint_mismatch")
        return {entry.entity_type for entry in manifest.entries}

    def _verify_evidence(
        self,
        raw_evidence: Any,
        *,
        plan: CadExecutionPlanV1,
        entity_types: set[str],
        issued_at: str | None,
    ) -> tuple[
        tuple[Phase8CapabilityEvidence, ...],
        dict[str, str],
        tuple[str, ...],
    ]:
        if not isinstance(raw_evidence, Sequence) or isinstance(
            raw_evidence, (str, bytes)
        ):
            raise AgentExecutionError("capability_missing")
        try:
            evidence = tuple(
                item
                if isinstance(item, Phase8CapabilityEvidence)
                else Phase8CapabilityEvidence.model_validate(item)
                for item in raw_evidence
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise AgentExecutionError("capability_missing") from error
        required = set(plan.required_capabilities)
        if (
            len(evidence) != len(required)
            or {item.capability_key for item in evidence} != required
        ):
            raise AgentExecutionError("capability_missing")

        command_time = (
            datetime.now(timezone.utc)
            if issued_at is None
            else _timestamp(issued_at, "capability_missing")
        )
        now = datetime.now(timezone.utc)
        pins = plan.execution_pins
        states: dict[str, str] = {}
        packs: set[str] = set()
        for item in evidence:
            if item.evidence_digest != canonical_phase8_capability_evidence_digest(
                item
            ):
                raise AgentExecutionError("capability_missing")
            valid_until = _timestamp(item.valid_until, "capability_missing")
            if not (
                _timestamp(item.issued_at, "capability_missing")
                <= command_time
                < valid_until
                and now < valid_until
            ):
                raise AgentExecutionError("capability_missing")
            if (
                item.evidence_authority != "gateway_server"
                or item.device_id != plan.device_id
                or item.runtime_id != pins.runtime_id
                or item.host_family != pins.host_family
                or item.package_hash != pins.package_hash
                or item.capability_manifest_hash != pins.capability_manifest_hash
                or item.operation_registry_hash != pins.operation_registry_hash
                or item.package_signature_verified is not True
            ):
                raise AgentExecutionError("capability_missing")
            expected_entity = _capability_entity_type(item.capability_key)
            if (
                expected_entity is None
                and item.entity_type != "ALL"
                and item.entity_type not in entity_types
            ) or (
                expected_entity is not None
                and (
                    item.entity_type != expected_entity
                    or item.entity_type not in entity_types
                )
            ):
                raise AgentExecutionError("capability_missing")
            pack = item.operation_pack
            if (
                pack not in self._policy.operation_pack_allowlist
                or any(marker in pack.casefold() for marker in _FORBIDDEN_PACK_MARKERS)
            ):
                raise AgentExecutionError("capability_missing")
            states[item.capability_key] = item.support_state
            packs.add(pack)
        if not packs:
            raise AgentExecutionError("capability_missing")
        if self._policy.rollout_policy_epoch < 1:
            raise AgentExecutionError("rollout_policy_mismatch")
        return evidence, states, tuple(sorted(packs))

    @staticmethod
    def _effective_capabilities(
        required: list[str],
        local_states: Mapping[str, str],
        server_states: Mapping[str, str],
        *,
        command_kind: str,
    ) -> dict[str, str]:
        minimum = "preview_only" if command_kind == "program_preview" else "lab_commit"
        needed_rank = _SUPPORT_RANK[minimum]
        effective: dict[str, str] = {}
        for key in required:
            local = local_states.get(key)
            server = server_states.get(key)
            if local not in _SUPPORT_RANK or server not in _SUPPORT_RANK:
                raise AgentExecutionError("capability_missing")
            state = min((local, server), key=lambda item: _SUPPORT_RANK[item])
            if _SUPPORT_RANK[state] < needed_rank:
                raise AgentExecutionError("capability_missing")
            effective[key] = state
        return effective

    @staticmethod
    def _verify_approval(
        approval: Phase8ApprovalBinding,
        *,
        plan: CadExecutionPlanV1,
        binding: ExecutionBindingV1,
        device_id: str | None,
        job_id: str | None,
        command_id: str | None,
        idempotency_key: str | None,
        preview_id: str | None,
        preview_digest: str | None,
        preview_expires_at: str | None,
        receipt_id: str | None,
    ) -> None:
        expected = {
            "device_id": device_id,
            "document_id": plan.document_id,
            "document_revision": plan.expected_document_revision,
            "job_id": job_id,
            "command_id": command_id,
            "idempotency_key": idempotency_key,
            "source_digest": plan.source_digest,
            "execution_plan_digest": plan.execution_plan_digest,
            "execution_binding_digest": binding.execution_binding_digest,
            "expansion_digest": plan.expansion_digest,
            "effect_manifest_digest": plan.effect_manifest_digest,
            "target_refs_digest": plan.target_refs_digest,
            "validation_profiles_digest": plan.validation_profiles_digest,
            "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
            "hard_budgets_digest": plan.hard_budgets_digest,
            "preview_id": preview_id,
            "preview_digest": preview_digest,
            "preview_expires_at": preview_expires_at,
            "receipt_id": receipt_id,
        }
        if any(getattr(approval, field) != exact for field, exact in expected.items()):
            raise AgentExecutionError("approval_binding_mismatch")
        if _timestamp(
            approval.preview_expires_at,
            "approval_binding_mismatch",
        ) <= datetime.now(timezone.utc):
            raise AgentExecutionError("approval_binding_mismatch")

    @staticmethod
    def _effect_identity(
        plan: CadExecutionPlanV1,
        binding: ExecutionBindingV1,
        approval: Phase8ApprovalBinding,
        *,
        evidence: tuple[Phase8CapabilityEvidence, ...],
        operation_packs: tuple[str, ...],
        capability_state_hash: str,
    ) -> str:
        return _canonical_digest(
            "cad.effect-identity/1",
            {
                "action": "program_commit",
                "intent_digest": approval.intent_digest,
                "approval_proof_digest": approval.approval_proof_digest,
                "idempotency_key": approval.idempotency_key,
                "execution_binding_digest": binding.execution_binding_digest,
                "execution_plan_digest": plan.execution_plan_digest,
                "effect_manifest_digest": plan.effect_manifest_digest,
                "target_refs_digest": plan.target_refs_digest,
                "document_id": plan.document_id,
                "document_revision_before": plan.expected_document_revision,
                "preview_id": binding.preview_id,
                "preview_digest": approval.preview_digest,
                "preview_expires_at": _host_timestamp(binding.preview_expires_at),
                "receipt_id": binding.receipt_id,
                "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
                "operation_packs": list(operation_packs),
                "capability_evidence_digests": sorted(
                    item.evidence_digest for item in evidence
                ),
                "capability_state_hash": capability_state_hash,
                **plan.execution_pins.model_dump(mode="json"),
            },
        )


def _parse_plan(value: Any) -> CadExecutionPlanV1:
    try:
        if isinstance(value, CadExecutionPlanV1):
            return value
        return parse_execution_plan_v1(_as_mapping(value, "plan_mismatch"))
    except (TypeError, ValueError, ValidationError) as error:
        raise AgentExecutionError("plan_mismatch") from error


def _parse_binding(value: Any) -> ExecutionBindingV1:
    try:
        if isinstance(value, ExecutionBindingV1):
            return value
        return parse_execution_binding_v1(_as_mapping(value, "binding_mismatch"))
    except (TypeError, ValueError, ValidationError) as error:
        raise AgentExecutionError("binding_mismatch") from error


def _parse_approval(value: Any) -> Phase8ApprovalBinding:
    if value is None:
        raise AgentExecutionError("approval_required")
    try:
        if isinstance(value, Phase8ApprovalBinding):
            return value
        return Phase8ApprovalBinding.model_validate(value)
    except (TypeError, ValueError, ValidationError) as error:
        raise AgentExecutionError("approval_binding_mismatch") from error


def _as_mapping(value: Any, code: str) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    if not isinstance(value, Mapping):
        raise AgentExecutionError(code)
    return dict(value)


def _canonical_digest(domain: str, value: dict[str, Any]) -> str:
    encoded = canonical_json({"domain": domain, "payload": value}).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise AgentExecutionError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AgentExecutionError(code) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AgentExecutionError(code)
    return parsed.astimezone(timezone.utc)


def _host_timestamp(value: str) -> str:
    parsed = _timestamp(value, "approval_binding_mismatch")
    return parsed.isoformat(timespec="microseconds").replace(
        "+00:00",
        "0+00:00",
    )


def _capability_entity_type(capability: str) -> str | None:
    parts = capability.split(".")
    if (
        len(parts) == 5
        and parts[:3] == ["cad", "op", "move"]
        and parts[-1] == "v1"
    ):
        return parts[3].upper()
    return None


def _pin_error(field: str) -> str:
    if field.startswith("runtime") or field.startswith("host"):
        return "runtime_mismatch"
    if field.startswith("package"):
        return "package_mismatch"
    if field.startswith("capability"):
        return "capability_mismatch"
    if field.startswith("operation_registry"):
        return "registry_mismatch"
    if field.startswith("rollout"):
        return "rollout_policy_mismatch"
    return "policy_mismatch"
