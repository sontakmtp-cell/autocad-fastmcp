"""Phase 7 fail-closed commit admission, trusted consent, and rollback admission."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Literal

from autocad_contracts import (
    ApprovalDecisionMessage,
    ApprovalRequestMessage,
    ConsentRecord,
    ExecutionIntentRecord,
    RollbackPlanRecord,
    approval_decision_proof_payload,
    approval_request_digest,
    canonical_receipt_id,
    canonical_json,
    execution_intent_digest,
    rollback_plan_digest,
)

from .contracts import (
    CadCommitInput,
    CadCommitOutput,
    CadCommitRollbackInput,
    CadCommitRollbackOutput,
    CadPreviewRollbackInput,
    CadPreviewRollbackOutput,
    Principal,
)
from .infrastructure.sqlite.phase7_repository import Phase7Repository
from .infrastructure.sqlite.repositories import RepositoryConflict
from .program_contract_adapter import (
    canonical_digest,
    execution_digest,
    program_wire_payload_hash,
)
from .program_services import ProgramGatewayService
from .services import GatewayError
from .identity import _verify


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _digest_text(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\0".join(parts).encode("utf-8")
    return f"{prefix}-{sha256(material).hexdigest()[:40]}"


@dataclass(frozen=True)
class Phase7AdmissionPolicy:
    phase7_c2_enabled: bool = False
    trusted_approval_enabled: bool = False
    device_local_approval_enabled: bool = False
    portal_recent_auth_approval_enabled: bool = False
    public_rollback_enabled: bool = False
    recovery_cases_enabled: bool = False
    phase6_direct_commit_lab_enabled: bool = False
    profile: str = "phase6_program"
    policy_version: str = "phase7-policy/1"
    job_deadline_seconds: float = 300.0
    recent_auth_max_age_seconds: int = 300


class Phase7AdmissionService:
    """Keeps effect-bearing jobs behind immutable intent and one-time consent."""

    def __init__(
        self,
        program_service: ProgramGatewayService,
        repository: Phase7Repository,
        policy: Phase7AdmissionPolicy,
    ) -> None:
        self.program_service = program_service
        self.repository = repository
        self.policy = policy
        self.database = repository.database
        self.rollback_preview_provider: Any | None = None

    async def commit(
        self,
        request: CadCommitInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadCommitOutput:
        if self.policy.phase6_direct_commit_lab_enabled:
            if self.policy.profile != "phase6_program":
                raise GatewayError("feature_disabled")
            return await self.program_service.commit(request, principal, correlation_id)
        self._require_phase7()
        if request.idempotency_key is None:
            raise GatewayError("invalid_request")
        material = await self._program_commit_material(request, principal)
        if material["receipt"] is not None:
            return self.program_service._commit_output(
                material["receipt"], correlation_id=correlation_id, duplicate=True
            )
        intent, consent = await self._create_or_reuse_program_intent(
            request=request,
            principal=principal,
            program=material["program"],
            preview=material["preview"],
            connection=material["connection"],
            payload=material["payload"],
        )
        if (
            consent is not None
            and consent["state"] == "requested"
            and intent["required_assurance"] == "device_local_confirmation"
        ):
            await self.dispatch_local_request(principal.subject, consent["consent_id"])
        return await self._current_or_release_program(
            intent=intent,
            consent=consent,
            program=material["program"],
            preview=material["preview"],
            payload=material["payload"],
            correlation_id=correlation_id,
        )

    async def preview_rollback(
        self,
        request: CadPreviewRollbackInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadPreviewRollbackOutput:
        del correlation_id
        self._require_rollback()
        if (request.receipt_id is None) == (request.checkpoint_id is None):
            raise GatewayError("invalid_request")
        checkpoint = (
            await self.repository.get_checkpoint(principal.subject, request.checkpoint_id)
            if request.checkpoint_id is not None
            else await self._checkpoint_by_receipt(principal.subject, request.receipt_id or "")
        )
        if checkpoint is None:
            # Old Phase 6 receipts deliberately have no eligible checkpoint.
            raise GatewayError("rollback_unavailable")
        existing = await self.repository.get_rollback_plan(
            principal.subject,
            _stable_id(
                "rollback-plan",
                principal.subject,
                checkpoint["checkpoint_id"],
                request.idempotency_key,
            ),
        )
        if existing is not None:
            if existing["checkpoint_id"] != checkpoint["checkpoint_id"]:
                raise GatewayError("idempotency_conflict")
            return self._rollback_preview_output(existing, duplicate=True)
        created_at = _now()
        expires_at = created_at + timedelta(seconds=120)
        plan_id = _stable_id(
            "rollback-plan",
            principal.subject,
            checkpoint["checkpoint_id"],
            request.idempotency_key,
        )
        rollback_execution_digest = canonical_digest(
            {
                "action": "rollback",
                "checkpoint_digest": checkpoint["checkpoint_digest"],
                "current_document_revision": checkpoint["document_revision_after"],
                "plan_id": plan_id,
            }
        )
        provider = self.rollback_preview_provider
        if provider is None:
            raise GatewayError("capability_missing")
        result = await provider(
            checkpoint,
            {
                "plan_id": plan_id,
                "rollback_execution_digest": rollback_execution_digest,
                "expires_at": _timestamp(expires_at),
                "attempt_id": "initial",
            },
        )
        if not isinstance(result, dict):
            raise GatewayError("invalid_response")
        current_revision = result.get("current_document_revision")
        conflicts = result.get("conflicts", [])
        runtime_pins = result.get("runtime_pins")
        policy_pins = result.get("policy_pins")
        if (
            not isinstance(current_revision, str)
            or not isinstance(conflicts, list)
            or not isinstance(runtime_pins, dict)
            or not isinstance(policy_pins, dict)
        ):
            raise GatewayError("invalid_response")
        value: dict[str, Any] = {
            "schema_version": "cad.rollback.plan/1",
            "plan_id": plan_id,
            "owner_subject": principal.subject,
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_digest": checkpoint["checkpoint_digest"],
            "original_receipt_id": checkpoint["original_receipt_id"],
            "document_id": checkpoint["document_id"],
            "current_document_revision": current_revision,
            "rollback_execution_digest": rollback_execution_digest,
            "entity_handles": [item["handle"] for item in checkpoint["created_entities"]],
            "conflicts": conflicts,
            "eligible": not conflicts,
            "runtime_pins": runtime_pins,
            "policy_pins": policy_pins,
            "plan_digest": "sha256:" + "0" * 64,
            "created_at": _timestamp(created_at),
            "expires_at": _timestamp(expires_at),
        }
        value["plan_digest"] = rollback_plan_digest(value)
        try:
            stored, duplicate = await self.repository.create_rollback_plan(
                RollbackPlanRecord.model_validate(value)
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None
        return self._rollback_preview_output(stored, duplicate=duplicate)

    async def commit_rollback(
        self,
        request: CadCommitRollbackInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadCommitRollbackOutput:
        self._require_rollback()
        plan = await self.repository.get_rollback_plan(
            principal.subject, request.rollback_plan_id
        )
        if plan is None:
            raise GatewayError("not_found")
        if not plan["eligible"] or plan["conflicts"]:
            raise GatewayError("rollback_conflict")
        if _now() >= datetime.fromisoformat(plan["expires_at"]):
            raise GatewayError("rollback_plan_expired")
        provider = self.rollback_preview_provider
        if provider is None:
            raise GatewayError("capability_missing")
        checkpoint = await self.repository.get_checkpoint(
            principal.subject, plan["checkpoint_id"]
        )
        if checkpoint is None:
            raise GatewayError("rollback_unavailable")
        current = await provider(
            checkpoint,
            {
                "plan_id": plan["plan_id"],
                "rollback_execution_digest": plan["rollback_execution_digest"],
                "expires_at": plan["expires_at"],
                "attempt_id": f"recheck-{uuid.uuid4()}",
            },
        )
        if (
            current.get("current_document_revision") != plan["current_document_revision"]
            or current.get("conflicts")
            or current.get("runtime_pins") != plan["runtime_pins"]
            or current.get("policy_pins") != plan["policy_pins"]
        ):
            raise GatewayError("rollback_plan_stale")
        existing_receipt = await self._rollback_receipt_by_plan(
            principal.subject, plan["plan_id"]
        )
        if existing_receipt is not None:
            return self._rollback_commit_output(
                plan, state="succeeded", duplicate=True, receipt=existing_receipt
            )
        intent, consent, payload = await self._create_or_reuse_rollback_intent(
            request=request,
            principal=principal,
            checkpoint=checkpoint,
            plan=plan,
        )
        released = await self._current_or_release(
            intent=intent, consent=consent, payload=payload
        )
        if released is None:
            return self._rollback_commit_output(
                plan,
                state="awaiting_approval",
                duplicate=True,
                intent=intent,
                consent=consent,
            )
        job = released["job"]
        job = await self.program_service._wait(job, principal.subject, correlation_id)
        receipt = await self._rollback_receipt_by_plan(
            principal.subject, plan["plan_id"]
        )
        return self._rollback_commit_output(
            plan,
            state=job["state"],
            duplicate=released["job_existing"],
            intent=released["intent"],
            consent=consent,
            job=job,
            receipt=receipt,
        )

    async def read_intent(self, owner_subject: str, intent_id: str) -> str:
        return self._bounded(
            await self.repository.get_intent(owner_subject, intent_id)
        )

    async def read_consent(self, owner_subject: str, consent_id: str) -> str:
        return self._bounded(
            await self.repository.get_consent(owner_subject, consent_id)
        )

    async def read_checkpoint(self, owner_subject: str, checkpoint_id: str) -> str:
        return self._bounded(
            await self.repository.get_checkpoint(owner_subject, checkpoint_id)
        )

    async def read_rollback(self, owner_subject: str, rollback_id: str) -> str:
        return self._bounded(
            await self.repository.get_rollback_plan(owner_subject, rollback_id)
        )

    async def read_rollback_receipt(self, owner_subject: str, receipt_id: str) -> str:
        return self._bounded(
            await self.repository.get_rollback_receipt(owner_subject, receipt_id)
        )

    async def read_evidence(self, owner_subject: str, job_id: str) -> str:
        values = await self.repository.list_evidence(owner_subject, job_id, limit=100)
        if not values:
            raise GatewayError("not_found")
        return self._bounded({"job_id": job_id, "events": values})

    async def read_recovery(self, owner_subject: str, case_id: str) -> str:
        return self._bounded(
            await self.repository.get_recovery_case(owner_subject, case_id)
        )

    async def portal_intent(self, owner_subject: str, intent_id: str) -> dict[str, Any]:
        intent = await self.repository.get_intent(owner_subject, intent_id)
        if intent is None:
            raise GatewayError("not_found")
        return intent

    async def portal_consent(
        self, owner_subject: str, consent_id: str
    ) -> dict[str, Any] | None:
        consent = await self.repository.get_consent(owner_subject, consent_id)
        if consent is None:
            raise GatewayError("not_found")
        intent = await self.repository.get_intent(owner_subject, consent["intent_id"])
        if intent is None:
            raise GatewayError("not_found")
        return {
            "consent": consent,
            "intent": intent,
            "decision_nonce": self._consent_nonce(consent),
        }

    async def portal_decide(
        self,
        *,
        owner_subject: str,
        consent_id: str,
        decision: Literal["approved", "denied"],
        intent_digest: str,
        consent_version: int,
        nonce: str,
        actor_issuer: str,
        actor_subject: str,
        auth_time: int | float | None,
    ) -> dict[str, Any]:
        if not (
            self.policy.phase7_c2_enabled
            and self.policy.trusted_approval_enabled
            and self.policy.portal_recent_auth_approval_enabled
        ):
            raise GatewayError("feature_disabled")
        self._require_recent_auth(auth_time)
        consent = await self.repository.get_consent(owner_subject, consent_id)
        if consent is None:
            raise GatewayError("not_found")
        intent = await self.repository.get_intent(owner_subject, consent["intent_id"])
        if intent is None:
            raise GatewayError("not_found")
        self._validate_decision_binding(
            consent, intent, intent_digest, consent_version, nonce
        )
        now = _timestamp()
        try:
            decided = await self.repository.transition_consent(
                owner_subject=owner_subject,
                consent_id=consent_id,
                target=decision,
                expected_version=consent["state_version"],
                transition_at=now,
                decision_source="portal_recent_auth",
                decision_principal={
                    "issuer": actor_issuer,
                    "subject": actor_subject,
                },
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None
        if decision == "denied":
            await self._deny_intent(intent, consent_id)
            return {"intent": await self.repository.get_intent(owner_subject, intent["intent_id"]), "consent": decided}
        return await self._approve_and_release(intent, decided)

    async def local_decide(
        self, decision: ApprovalDecisionMessage | dict[str, Any]
    ) -> dict[str, Any]:
        if not (
            self.policy.phase7_c2_enabled
            and self.policy.trusted_approval_enabled
            and self.policy.device_local_approval_enabled
        ):
            raise GatewayError("feature_disabled")
        try:
            message = (
                decision
                if isinstance(decision, ApprovalDecisionMessage)
                else ApprovalDecisionMessage.model_validate(decision)
            )
        except ValueError:
            raise GatewayError("invalid_request")
        consent, intent = await self._internal_consent(message.consent_id)
        if (
            intent["device_id"] != message.device_id
            or intent["intent_id"] != message.intent_id
        ):
            raise GatewayError("approval_binding_mismatch")
        connection = await self.program_service.registry.get(message.device_id)
        if (
            connection is None
            or connection.session_id != message.session_id
            or not await self.program_service.registry.is_current_and_fresh(connection)
        ):
            raise GatewayError("approval_session_replaced")
        generation, thumbprint = self._stable_device_identity(
            intent["owner_subject"], intent["device_id"]
        )
        if (
            generation != message.device_identity_generation
            or generation != intent["device_identity_generation"]
            or thumbprint != message.device_key_thumbprint
            or thumbprint != intent["device_key_thumbprint"]
        ):
            raise GatewayError("approval_binding_mismatch")
        request = self._approval_request(intent, consent, connection)
        if (
            message.approval_request_id != request.approval_request_id
            or message.approval_request_digest != request.approval_request_digest
        ):
            raise GatewayError("approval_binding_mismatch")
        self._validate_decision_binding(
            consent,
            intent,
            message.intent_digest,
            consent["consent_version"],
            message.challenge_nonce,
        )
        decided_at = datetime.fromisoformat(message.decided_at)
        if (
            decided_at > _now() + timedelta(seconds=30)
            or decided_at >= datetime.fromisoformat(consent["expires_at"])
        ):
            raise GatewayError("consent_expired")
        proof_payload = approval_decision_proof_payload(
            approval_request_id=message.approval_request_id,
            approval_request_digest=message.approval_request_digest,
            session_id=message.session_id or "",
            device_id=message.device_id or "",
            device_identity_generation=message.device_identity_generation,
            device_key_thumbprint=message.device_key_thumbprint,
            consent_id=message.consent_id,
            intent_id=message.intent_id,
            intent_digest=message.intent_digest,
            challenge_nonce=message.challenge_nonce,
            decision=message.decision,
            decided_at=message.decided_at,
        )
        try:
            _verify(
                self._device_public_key(intent["owner_subject"], intent["device_id"]),
                message.device_session_proof,
                proof_payload,
            )
        except ValueError:
            raise GatewayError("approval_proof_invalid") from None
        now = _timestamp()
        target = "approved" if message.decision == "approve" else "denied"
        try:
            decided = await self.repository.transition_consent(
                owner_subject=intent["owner_subject"],
                consent_id=consent["consent_id"],
                target=target,
                expected_version=consent["state_version"],
                transition_at=now,
                decision_source="device_local",
                decision_principal={
                    "issuer": "cad.agent/2",
                    "subject": intent["device_id"],
                },
                decision_device_id=intent["device_id"],
                decision_device_identity_generation=generation,
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None
        if target == "denied":
            await self._deny_intent(intent, consent["consent_id"])
            return {"intent": await self.repository.get_intent(intent["owner_subject"], intent["intent_id"]), "consent": decided}
        return await self._approve_and_release(intent, decided)

    async def dispatch_local_request(
        self, owner_subject: str, consent_id: str
    ) -> None:
        consent = await self.repository.get_consent(owner_subject, consent_id)
        if consent is None:
            raise GatewayError("not_found")
        intent = await self.repository.get_intent(owner_subject, consent["intent_id"])
        if intent is None:
            raise GatewayError("not_found")
        connection = await self.program_service.registry.get(intent["device_id"])
        if (
            connection is None
            or not await self.program_service.registry.is_current_and_fresh(connection)
        ):
            raise GatewayError("device_offline")
        request = self._approval_request(intent, consent, connection)
        await connection.send(request.model_dump(mode="json", exclude_none=True))

    async def _program_commit_material(
        self, request: CadCommitInput, principal: Principal
    ) -> dict[str, Any]:
        self.program_service._require_write_scope(principal)
        self.program_service._require_managed_write()
        preview = await self.program_service.program_repository.get_preview(
            principal.subject, request.preview_id
        )
        if preview is None:
            raise GatewayError("not_found")
        program = await self.program_service._require_program(
            principal.subject, preview["program_id"], preview["program_revision"]
        )
        self.program_service._require_allowed_device(program["device_id"])
        receipt = await self.program_service.program_repository.get_receipt_by_preview(
            principal.subject, preview["preview_id"]
        )
        if receipt is not None:
            return {
                "receipt": receipt,
                "program": program,
                "preview": preview,
                "connection": None,
                "payload": None,
            }
        if preview["invalidated_reason"]:
            raise GatewayError("binding_mismatch")
        if self.program_service._expired(preview["expires_at"]):
            await self.program_service.program_repository.invalidate_preview(
                preview["preview_id"], "preview_expired"
            )
            raise GatewayError("preview_expired")
        connection = await self.program_service._revalidate_program(
            program, preview=preview
        )
        if (
            connection.hard_pause
            or connection.paused
            or not connection.write_lock_enabled
        ):
            raise GatewayError("write_lock_disabled")
        receipt_id = canonical_receipt_id(preview["preview_id"])
        digest = execution_digest(
            action="commit",
            program_digest=program["program_digest"],
            binding_digest_value=preview["binding_digest"],
            nonce_id=receipt_id,
        )
        payload = self.program_service._job_payload(
            program,
            action="commit",
            execution={
                "preview_id": preview["preview_id"],
                "receipt_id": receipt_id,
                "preview_execution_digest": preview["execution_digest"],
                "preview_digest": preview["preview_digest"],
                "execution_digest": digest,
                "binding_digest": preview["binding_digest"],
            },
        )
        return {
            "receipt": None,
            "program": program,
            "preview": preview,
            "connection": connection,
            "payload": payload,
        }

    async def _create_or_reuse_program_intent(
        self,
        *,
        request: CadCommitInput,
        principal: Principal,
        program: dict[str, Any],
        preview: dict[str, Any],
        connection: Any,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        request_hash = canonical_digest(
            {
                "action": "program_commit",
                "owner_subject": principal.subject,
                "preview_id": preview["preview_id"],
                "preview_digest": preview["preview_digest"],
            }
        )
        key = request.idempotency_key or ""
        intent_id = _stable_id("intent", principal.subject, key)
        existing = await self.repository.get_intent(principal.subject, intent_id)
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise GatewayError("idempotency_conflict")
            consent = (
                await self.repository.get_consent(
                    principal.subject,
                    _stable_id("consent", existing["intent_id"], existing["intent_digest"]),
                )
                if existing["required_assurance"] != "none"
                else None
            )
            return existing, consent
        assurance = self._program_assurance(program)
        generation, thumbprint = self._stable_device_identity(
            principal.subject, program["device_id"]
        )
        runtime_pins, policy_pins = self._intent_pins(program, connection)
        created_at = _now()
        preview_expiry = datetime.fromisoformat(preview["expires_at"])
        expires_at = min(preview_expiry, created_at + timedelta(minutes=10))
        if expires_at <= created_at:
            raise GatewayError("preview_expired")
        value: dict[str, Any] = {
            "schema_version": "cad.execution-intent/1",
            "intent_id": intent_id,
            "intent_version": 1,
            "owner_subject": principal.subject,
            "actor_principal": {"issuer": "gateway", "subject": principal.subject},
            "action": "program_commit",
            "state": "awaiting_approval" if assurance != "none" else "ready",
            "state_version": 0,
            "device_id": program["device_id"],
            "device_identity_generation": generation,
            "device_key_thumbprint": thumbprint,
            "document_id": program["document_id"],
            "expected_document_revision": program["expected_document_revision"],
            "program_id": program["program_id"],
            "program_revision": program["program_revision"],
            "program_digest": program["program_digest"],
            "preview_id": preview["preview_id"],
            "preview_digest": preview["preview_digest"],
            "preview_execution_digest": preview["execution_digest"],
            "preview_expires_at": preview["expires_at"],
            "deterministic_receipt_id": payload["execution"]["receipt_id"],
            "commit_execution_digest": payload["execution"]["execution_digest"],
            "runtime_pins": runtime_pins,
            "policy_pins": policy_pins,
            "risk_class": program["risk_class"],
            "required_assurance": assurance,
            "trusted_effect_summary": self._effect_summary(program, preview),
            "idempotency_key": key,
            "request_hash": request_hash,
            "intent_digest": "sha256:" + "0" * 64,
            "created_at": _timestamp(created_at),
            "expires_at": _timestamp(expires_at),
            "consent_id": None,
            "released_job_id": None,
        }
        value["intent_digest"] = execution_intent_digest(value)
        try:
            intent, _ = await self.repository.create_intent(
                ExecutionIntentRecord.model_validate(value)
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None
        consent = await self._create_consent(intent) if assurance != "none" else None
        return intent, consent

    async def _create_or_reuse_rollback_intent(
        self,
        *,
        request: CadCommitRollbackInput,
        principal: Principal,
        checkpoint: dict[str, Any],
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        request_hash = canonical_digest(
            {
                "action": "rollback_commit",
                "owner_subject": principal.subject,
                "plan_id": plan["plan_id"],
                "plan_digest": plan["plan_digest"],
            }
        )
        intent_id = _stable_id(
            "intent", principal.subject, f"rollback:{request.idempotency_key}"
        )
        receipt_id = _stable_id("rollback-receipt", plan["plan_id"])
        payload = {
            "kind": "rollback_commit",
            "effect_class": "write",
            "binding": self._rollback_binding(checkpoint, plan),
            "arguments": {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "checkpoint_digest": checkpoint["checkpoint_digest"],
                "rollback_plan_id": plan["plan_id"],
                "rollback_plan_digest": plan["plan_digest"],
                "rollback_execution_digest": plan["rollback_execution_digest"],
                "rollback_receipt_id": receipt_id,
                "expires_at": plan["expires_at"],
            },
            "intent_id": intent_id,
            "intent_digest": "",
        }
        existing = await self.repository.get_intent(principal.subject, intent_id)
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise GatewayError("idempotency_conflict")
            payload["intent_digest"] = existing["intent_digest"]
            consent = await self.repository.get_consent(
                principal.subject,
                _stable_id("consent", existing["intent_id"], existing["intent_digest"]),
            )
            if consent is None:
                raise GatewayError("idempotency_state_invalid")
            return existing, consent, payload
        # Checkpoints do not carry device_id in v1; resolve it through the original program.
        program = await self.program_service._require_program(
            principal.subject,
            checkpoint["program_id"],
            checkpoint["program_revision"],
        )
        preview = await self.program_service.program_repository.get_preview(
            principal.subject, checkpoint["preview_id"]
        )
        if (
            preview is None
            or preview["program_id"] != checkpoint["program_id"]
            or preview["program_revision"] != checkpoint["program_revision"]
            or preview["preview_digest"] != checkpoint["preview_digest"]
        ):
            raise GatewayError("rollback_binding_mismatch")
        generation, thumbprint = self._stable_device_identity(
            principal.subject, program["device_id"]
        )
        now = _now()
        value: dict[str, Any] = {
            "schema_version": "cad.execution-intent/1",
            "intent_id": intent_id,
            "intent_version": 1,
            "owner_subject": principal.subject,
            "actor_principal": {"issuer": "gateway", "subject": principal.subject},
            "action": "rollback_commit",
            "state": "awaiting_approval",
            "state_version": 0,
            "device_id": program["device_id"],
            "device_identity_generation": generation,
            "device_key_thumbprint": thumbprint,
            "document_id": checkpoint["document_id"],
            "expected_document_revision": plan["current_document_revision"],
            "program_id": checkpoint["program_id"],
            "program_revision": checkpoint["program_revision"],
            "program_digest": checkpoint["program_digest"],
            "preview_id": checkpoint["preview_id"],
            "preview_digest": checkpoint["preview_digest"],
            "preview_execution_digest": preview["execution_digest"],
            "preview_expires_at": plan["expires_at"],
            "deterministic_receipt_id": receipt_id,
            "commit_execution_digest": plan["rollback_execution_digest"],
            "runtime_pins": plan["runtime_pins"],
            "policy_pins": plan["policy_pins"],
            "risk_class": "destructive",
            "required_assurance": "user_recent_auth",
            "trusted_effect_summary": [
                {
                    "kind": "erase_entities",
                    "count": len(plan["entity_handles"]),
                    "summary": "Remove only entities bound to the Phase 7 checkpoint.",
                }
            ],
            "idempotency_key": f"rollback:{request.idempotency_key}",
            "request_hash": request_hash,
            "intent_digest": "sha256:" + "0" * 64,
            "created_at": _timestamp(now),
            "expires_at": plan["expires_at"],
            "consent_id": None,
            "released_job_id": None,
        }
        value["intent_digest"] = execution_intent_digest(value)
        intent, _ = await self.repository.create_intent(
            ExecutionIntentRecord.model_validate(value)
        )
        payload["intent_digest"] = intent["intent_digest"]
        return intent, await self._create_consent(intent), payload

    async def _create_consent(self, intent: dict[str, Any]) -> dict[str, Any]:
        consent_id = _stable_id(
            "consent", intent["intent_id"], intent["intent_digest"]
        )
        existing = await self.repository.get_consent(
            intent["owner_subject"], consent_id
        )
        if existing is not None:
            return existing
        value = {
            "schema_version": "cad.consent/1",
            "consent_id": consent_id,
            "consent_version": 1,
            "owner_subject": intent["owner_subject"],
            "intent_id": intent["intent_id"],
            "intent_version": intent["intent_version"],
            "intent_digest": intent["intent_digest"],
            "required_assurance": intent["required_assurance"],
            "state": "requested",
            "state_version": 0,
            "challenge_nonce_hash": _digest_text(
                self._consent_nonce_from_ids(consent_id, intent["intent_digest"])
            ),
            "requested_at": intent["created_at"],
            "expires_at": intent["expires_at"],
        }
        try:
            stored, _ = await self.repository.create_consent(
                ConsentRecord.model_validate(value)
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None
        return stored

    async def _current_or_release_program(
        self,
        *,
        intent: dict[str, Any],
        consent: dict[str, Any] | None,
        program: dict[str, Any],
        preview: dict[str, Any],
        payload: dict[str, Any],
        correlation_id: str,
    ) -> CadCommitOutput:
        if intent["state"] in {"denied", "expired", "invalidated", "cancelled"}:
            raise GatewayError(f"intent_{intent['state']}")
        if intent["state"] == "released":
            job = await self.program_service.repository.get_job(
                intent["owner_subject"], intent["released_job_id"]
            )
            if job is None:
                raise GatewayError("idempotency_state_invalid")
            receipt = await self.program_service.program_repository.get_receipt_by_job(
                intent["owner_subject"], job["job_id"]
            )
            if receipt is not None:
                return self.program_service._commit_output(
                    receipt, correlation_id=correlation_id, duplicate=True
                )
            return self._commit_output(
                program,
                preview,
                intent,
                consent,
                correlation_id,
                state=job["state"],
                job=job,
                duplicate=True,
            )
        if consent is not None and consent["state"] == "requested":
            return self._commit_output(
                program,
                preview,
                intent,
                consent,
                correlation_id,
                state="awaiting_approval",
                job=None,
                duplicate=True,
            )
        released = await self._current_or_release(
            intent=intent, consent=consent, payload=payload
        )
        if released is None:
            return self._commit_output(
                program,
                preview,
                intent,
                consent,
                correlation_id,
                state="awaiting_approval",
                job=None,
                duplicate=True,
            )
        job = await self.program_service._wait(
            released["job"], intent["owner_subject"], correlation_id
        )
        receipt = await self.program_service.program_repository.get_receipt_by_job(
            intent["owner_subject"], job["job_id"]
        )
        if receipt is not None:
            return self.program_service._commit_output(
                receipt,
                correlation_id=correlation_id,
                duplicate=released["job_existing"],
            )
        return self._commit_output(
            program,
            preview,
            released["intent"],
            consent,
            correlation_id,
            state=job["state"],
            job=job,
            duplicate=released["job_existing"],
        )

    async def _current_or_release(
        self,
        *,
        intent: dict[str, Any],
        consent: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if consent is not None and consent["state"] != "approved":
            return None
        await self._revalidate_intent(intent)
        payload = json.loads(json.dumps(payload))
        kind = intent["action"]
        if kind == "program_commit":
            payload["execution"]["intent_id"] = intent["intent_id"]
            payload["execution"]["intent_digest"] = intent["intent_digest"]
        else:
            payload["intent_id"] = intent["intent_id"]
            payload["intent_digest"] = intent["intent_digest"]
        job_id = _stable_id("job", intent["intent_id"])
        command_id = _stable_id("command", intent["intent_id"])
        deadline_at = _timestamp(
            datetime.fromisoformat(intent["created_at"])
            + timedelta(seconds=self.policy.job_deadline_seconds)
        )
        try:
            payload_hash = (
                program_wire_payload_hash(
                    kind="program_commit", effect_class="write", payload=payload
                )
                if kind == "program_commit"
                else canonical_digest(payload)
            )
            return await self.repository.release_intent(
                owner_subject=intent["owner_subject"],
                intent_id=intent["intent_id"],
                job_id=job_id,
                command_id=command_id,
                idempotency_key=_stable_id("release", intent["intent_id"]),
                payload_hash=payload_hash.removeprefix("sha256:"),
                payload=payload,
                deadline_at=deadline_at,
                kind=kind,
                expected_intent_version=intent["state_version"],
                consumed_at=_timestamp(),
                consent_id=consent["consent_id"] if consent else None,
                expected_consent_version=consent["state_version"] if consent else None,
            )
        except RepositoryConflict as error:
            if error.code == "cas_conflict":
                current = await self.repository.get_intent(
                    intent["owner_subject"], intent["intent_id"]
                )
                if current and current["state"] == "released":
                    job = await self.program_service.repository.get_job(
                        intent["owner_subject"], current["released_job_id"]
                    )
                    if job is not None:
                        return {
                            "intent": current,
                            "job": job,
                            "job_existing": True,
                        }
            raise GatewayError(self._repository_code(error.code)) from None

    async def _approve_and_release(
        self, intent: dict[str, Any], consent: dict[str, Any]
    ) -> dict[str, Any]:
        if intent["action"] == "program_commit":
            preview = await self.program_service.program_repository.get_preview(
                intent["owner_subject"], intent["preview_id"]
            )
            program = await self.program_service._require_program(
                intent["owner_subject"],
                intent["program_id"],
                intent["program_revision"],
            )
            if preview is None:
                raise GatewayError("not_found")
            payload = self.program_service._job_payload(
                program,
                action="commit",
                execution={
                    "preview_id": intent["preview_id"],
                    "receipt_id": intent["deterministic_receipt_id"],
                    "preview_execution_digest": intent["preview_execution_digest"],
                    "preview_digest": intent["preview_digest"],
                    "execution_digest": intent["commit_execution_digest"],
                    "binding_digest": preview["binding_digest"],
                },
            )
        else:
            plan_id = self._rollback_plan_id_from_intent(intent)
            plan = await self.repository.get_rollback_plan(
                intent["owner_subject"], plan_id
            )
            checkpoint = await self.repository.get_checkpoint(
                intent["owner_subject"],
                plan["checkpoint_id"] if plan else "",
            )
            if plan is None or checkpoint is None:
                raise GatewayError("not_found")
            if _now() >= datetime.fromisoformat(plan["expires_at"]):
                raise GatewayError("rollback_plan_expired")
            provider = self.rollback_preview_provider
            if provider is None:
                raise GatewayError("capability_missing")
            current = await provider(
                checkpoint,
                {
                    "plan_id": plan["plan_id"],
                    "rollback_execution_digest": plan["rollback_execution_digest"],
                    "expires_at": plan["expires_at"],
                    "attempt_id": f"release-{uuid.uuid4()}",
                },
            )
            if (
                not isinstance(current, dict)
                or current.get("current_document_revision")
                != plan["current_document_revision"]
                or current.get("conflicts")
                or current.get("runtime_pins") != plan["runtime_pins"]
                or current.get("policy_pins") != plan["policy_pins"]
            ):
                raise GatewayError("rollback_plan_stale")
            payload = {
                "kind": "rollback_commit",
                "effect_class": "write",
                "binding": self._rollback_binding(checkpoint, plan),
                "arguments": {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "checkpoint_digest": checkpoint["checkpoint_digest"],
                    "rollback_plan_id": plan["plan_id"],
                    "rollback_plan_digest": plan["plan_digest"],
                    "rollback_execution_digest": plan["rollback_execution_digest"],
                    "rollback_receipt_id": intent["deterministic_receipt_id"],
                    "expires_at": plan["expires_at"],
                },
                "intent_id": intent["intent_id"],
                "intent_digest": intent["intent_digest"],
            }
        released = await self._current_or_release(
            intent=intent, consent=consent, payload=payload
        )
        if released is None:
            raise GatewayError("consent_not_approved")
        if intent["action"] == "program_commit":
            released["job"] = await self.program_service._wait(
                released["job"],
                intent["owner_subject"],
                str(uuid.uuid4()),
            )
        released["consent"] = consent
        return released

    async def _revalidate_intent(self, intent: dict[str, Any]) -> None:
        if not self.policy.phase7_c2_enabled:
            await self._invalidate(intent)
            raise GatewayError("feature_disabled")
        if _now() >= datetime.fromisoformat(intent["expires_at"]):
            raise GatewayError("intent_expired")
        program = await self.program_service._require_program(
            intent["owner_subject"], intent["program_id"], intent["program_revision"]
        )
        preview = await self.program_service.program_repository.get_preview(
            intent["owner_subject"], intent["preview_id"]
        )
        if intent["action"] == "program_commit":
            if preview is None:
                await self._invalidate(intent)
                raise GatewayError("binding_mismatch")
            try:
                connection = await self.program_service._revalidate_program(
                    program, preview=preview
                )
            except GatewayError:
                await self._invalidate(intent)
                raise
            if (
                connection.hard_pause
                or connection.paused
                or not connection.write_lock_enabled
            ):
                await self._invalidate(intent)
                raise GatewayError("write_lock_disabled")
            runtime_pins, policy_pins = self._intent_pins(program, connection)
            if (
                runtime_pins != intent["runtime_pins"]
                or policy_pins != intent["policy_pins"]
            ):
                await self._invalidate(intent)
                raise GatewayError("binding_mismatch")
        generation, thumbprint = self._stable_device_identity(
            intent["owner_subject"], intent["device_id"]
        )
        if (
            generation != intent["device_identity_generation"]
            or thumbprint != intent["device_key_thumbprint"]
        ):
            await self._invalidate(intent)
            raise GatewayError("binding_mismatch")

    async def _invalidate(self, intent: dict[str, Any]) -> None:
        try:
            await self.repository.transition_intent(
                owner_subject=intent["owner_subject"],
                intent_id=intent["intent_id"],
                target="invalidated",
                expected_version=intent["state_version"],
            )
        except RepositoryConflict:
            pass
        consent = await self.repository.get_consent(
            intent["owner_subject"],
            _stable_id("consent", intent["intent_id"], intent["intent_digest"]),
        )
        if consent and consent["state"] in {"requested", "approved"}:
            try:
                await self.repository.transition_consent(
                    owner_subject=intent["owner_subject"],
                    consent_id=consent["consent_id"],
                    target="invalidated",
                    expected_version=consent["state_version"],
                    transition_at=_timestamp(),
                )
            except RepositoryConflict:
                pass

    async def _deny_intent(
        self, intent: dict[str, Any], consent_id: str
    ) -> None:
        try:
            await self.repository.transition_intent(
                owner_subject=intent["owner_subject"],
                intent_id=intent["intent_id"],
                target="denied",
                expected_version=intent["state_version"],
                consent_id=consent_id,
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None

    def _program_assurance(self, program: dict[str, Any]) -> str:
        if not self.policy.trusted_approval_enabled:
            raise GatewayError("feature_disabled")
        # Portal recent-auth is stricter than device-local confirmation.
        if self.policy.portal_recent_auth_approval_enabled:
            return "user_recent_auth"
        if self.policy.device_local_approval_enabled:
            return "device_local_confirmation"
        raise GatewayError("feature_disabled")

    def _stable_device_identity(
        self, owner_subject: str, device_id: str
    ) -> tuple[int, str]:
        if not device_id:
            raise GatewayError("not_found")
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT c.key_fingerprint, c.revoked_at "
                "FROM device_credentials c JOIN devices d ON d.device_id = c.device_id "
                "WHERE d.owner_subject = ? AND c.device_id = ?",
                (owner_subject, device_id),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise GatewayError("device_identity_invalid")
        return 1, f"sha256:{row['key_fingerprint']}"

    def _device_public_key(self, owner_subject: str, device_id: str) -> str:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT c.public_key, c.revoked_at "
                "FROM device_credentials c JOIN devices d ON d.device_id = c.device_id "
                "WHERE d.owner_subject = ? AND c.device_id = ?",
                (owner_subject, device_id),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise GatewayError("device_identity_invalid")
        return str(row["public_key"])

    def _approval_request(
        self,
        intent: dict[str, Any],
        consent: dict[str, Any],
        connection: Any,
    ) -> ApprovalRequestMessage:
        effect_count = sum(
            int(item["count"]) for item in intent["trusted_effect_summary"]
        )
        operation_count = max(
            1, min(len(intent["trusted_effect_summary"]), 256)
        )
        value: dict[str, Any] = {
            "protocol_version": "cad.agent/2",
            "message_type": "approval_request",
            "message_id": _stable_id(
                "approval-message", consent["consent_id"], connection.session_id
            ),
            "correlation_id": _stable_id("approval", intent["intent_id"]),
            "session_id": connection.session_id,
            "device_id": intent["device_id"],
            "sequence": 0,
            "issued_at": consent["requested_at"],
            "deadline_at": consent["expires_at"],
            "approval_request_id": _stable_id(
                "approval-request", consent["consent_id"], connection.session_id
            ),
            "intent_id": intent["intent_id"],
            "consent_id": consent["consent_id"],
            "intent_digest": intent["intent_digest"],
            "challenge_nonce": self._consent_nonce(consent),
            "expires_at": consent["expires_at"],
            "required_assurance": "device_local_confirmation",
            "device_identity_generation": intent["device_identity_generation"],
            "device_key_thumbprint": intent["device_key_thumbprint"],
            "trusted_summary": {
                "operation": intent["action"],
                "operation_summary": intent["trusted_effect_summary"][0]["summary"],
                "document_name": connection.document_name or "drawing.dwg",
                "document_id": intent["document_id"],
                "operation_count": operation_count,
                "entity_count": min(effect_count, 256),
                "runtime_label": (
                    f"{intent['runtime_pins']['host_family']} "
                    f"{intent['runtime_pins']['host_version']}"
                ),
                "runtime_id": intent["runtime_pins"]["runtime_id"],
                "package_id": intent["runtime_pins"]["host_package_id"],
                "package_version": intent["runtime_pins"]["host_package_version"],
                "registry_version": intent["policy_pins"]["registry_version"],
                "risk_class": intent["risk_class"],
                "preview_created_at": intent["created_at"],
                "warnings": [],
                "support_id": intent["intent_id"],
            },
            "approval_request_digest": "sha256:" + "0" * 64,
        }
        value["approval_request_digest"] = approval_request_digest(value)
        return ApprovalRequestMessage.model_validate(value)

    @staticmethod
    def _intent_pins(
        program: dict[str, Any], connection: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        agent_hash = getattr(connection, "package_manifest_hash", None)
        agent_version = getattr(connection, "agent_version", None)
        if not isinstance(agent_hash, str) or not agent_hash:
            raise GatewayError("package_mismatch")
        if not isinstance(agent_version, str) or not agent_version:
            raise GatewayError("package_mismatch")
        pins = program["pins"]
        runtime = {
            "runtime_id": pins["runtime_id"],
            "runtime_role": pins["runtime_role"],
            "host_family": pins["host_family"],
            "host_version": pins["host_version"],
            "agent_package_id": "autocad.desktop_agent",
            "agent_package_version": agent_version,
            "agent_package_hash": (
                agent_hash if agent_hash.startswith("sha256:") else f"sha256:{agent_hash}"
            ),
            "host_package_id": pins["package_id"],
            "host_package_version": pins["package_version"],
            "host_package_hash": pins["package_hash"],
        }
        policy = {
            "capability_manifest_hash": (
                pins["capability_manifest_hash"]
                if pins["capability_manifest_hash"].startswith("sha256:")
                else f"sha256:{pins['capability_manifest_hash']}"
            ),
            "operation_registry_hash": pins["operation_registry_hash"],
            "registry_version": pins["registry_version"],
            "policy_version": pins["policy_version"],
        }
        return runtime, policy

    @staticmethod
    def _rollback_binding(
        checkpoint: dict[str, Any], plan: dict[str, Any]
    ) -> dict[str, Any]:
        runtime = plan["runtime_pins"]
        policy = plan["policy_pins"]
        return {
            "program_digest": checkpoint["program_digest"],
            "execution_digest": plan["rollback_execution_digest"],
            "document_id": checkpoint["document_id"],
            "document_revision": plan["current_document_revision"],
            "runtime_id": runtime["runtime_id"],
            "runtime_role": runtime["runtime_role"],
            "host_family": runtime["host_family"],
            "host_version": runtime["host_version"],
            "package_id": runtime["host_package_id"],
            "package_version": runtime["host_package_version"],
            "package_hash": runtime["host_package_hash"],
            "capability_manifest_hash": policy["capability_manifest_hash"],
            "operation_registry_version": policy["registry_version"],
            "operation_registry_hash": policy["operation_registry_hash"],
            "policy_version": policy["policy_version"],
        }

    @staticmethod
    def _effect_summary(
        program: dict[str, Any], preview: dict[str, Any]
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        entity_count = int(preview.get("planned_entity_count") or 0)
        layer_count = int(preview.get("planned_layer_count") or 0)
        if entity_count:
            values.append(
                {
                    "kind": "create_entities",
                    "count": min(entity_count, 256),
                    "summary": f"Create {entity_count} bounded drawing entities.",
                }
            )
        if layer_count:
            values.append(
                {
                    "kind": "ensure_layers",
                    "count": min(layer_count, 256),
                    "summary": f"Ensure {layer_count} drawing layers exist.",
                }
            )
        if not values:
            values.append(
                {
                    "kind": "document_change",
                    "count": min(
                        len(program["semantic"].get("operations", [])), 256
                    ),
                    "summary": "Apply the exact server-pinned create-only program.",
                }
            )
        return values

    def _validate_decision_binding(
        self,
        consent: dict[str, Any],
        intent: dict[str, Any],
        intent_digest: Any,
        consent_version: Any,
        nonce: Any,
    ) -> None:
        if consent["state"] != "requested":
            raise GatewayError("approval_replay")
        if _now() >= datetime.fromisoformat(consent["expires_at"]):
            raise GatewayError("consent_expired")
        if (
            intent_digest != intent["intent_digest"]
            or consent["intent_digest"] != intent["intent_digest"]
            or consent_version != consent["consent_version"]
            or not isinstance(nonce, str)
            or _digest_text(nonce) != consent["challenge_nonce_hash"]
        ):
            raise GatewayError("approval_binding_mismatch")

    def _require_recent_auth(self, auth_time: int | float | None) -> None:
        if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)):
            raise GatewayError("recent_auth_required")
        age = _now().timestamp() - float(auth_time)
        if age < -30 or age > self.policy.recent_auth_max_age_seconds:
            raise GatewayError("recent_auth_required")

    def _consent_nonce(self, consent: dict[str, Any]) -> str:
        return self._consent_nonce_from_ids(
            consent["consent_id"], consent["intent_digest"]
        )

    @staticmethod
    def _consent_nonce_from_ids(consent_id: str, intent_digest: str) -> str:
        # Nonce is a challenge binding, never an approval credential by itself.
        return sha256(
            f"cad.consent/1\0{consent_id}\0{intent_digest}".encode("utf-8")
        ).hexdigest()

    async def _internal_consent(
        self, consent_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT owner_subject, intent_id FROM consents WHERE consent_id = ?",
                (consent_id,),
            ).fetchone()
        if row is None:
            raise GatewayError("not_found")
        consent = await self.repository.get_consent(row["owner_subject"], consent_id)
        intent = await self.repository.get_intent(
            row["owner_subject"], row["intent_id"]
        )
        if consent is None or intent is None:
            raise GatewayError("not_found")
        return consent, intent

    async def _checkpoint_by_receipt(
        self, owner_subject: str, receipt_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT checkpoint_id FROM rollback_checkpoints "
                "WHERE owner_subject = ? AND original_receipt_id = ?",
                (owner_subject, receipt_id),
            ).fetchone()
        return (
            await self.repository.get_checkpoint(owner_subject, row["checkpoint_id"])
            if row is not None
            else None
        )

    async def _rollback_receipt_by_plan(
        self, owner_subject: str, plan_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT rollback_receipt_id FROM rollback_receipts "
                "WHERE owner_subject = ? AND rollback_plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
        return (
            await self.repository.get_rollback_receipt(
                owner_subject, row["rollback_receipt_id"]
            )
            if row is not None
            else None
        )

    def _rollback_plan_id_from_intent(self, intent: dict[str, Any]) -> str:
        # The immutable request hash is not reversible; locate the one plan whose
        # execution digest is bound by this rollback intent.
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT plan_id FROM rollback_plans WHERE owner_subject = ? "
                "AND rollback_execution_digest = ?",
                (intent["owner_subject"], intent["commit_execution_digest"]),
            ).fetchone()
        if row is None:
            raise GatewayError("not_found")
        return str(row["plan_id"])

    def _commit_output(
        self,
        program: dict[str, Any],
        preview: dict[str, Any],
        intent: dict[str, Any],
        consent: dict[str, Any] | None,
        correlation_id: str,
        *,
        state: str,
        job: dict[str, Any] | None,
        duplicate: bool,
    ) -> CadCommitOutput:
        return CadCommitOutput(
            correlation_id=correlation_id,
            program_id=program["program_id"],
            program_revision=program["program_revision"],
            preview_id=preview["preview_id"],
            job_id=job["job_id"] if job else None,
            state=state,
            program_digest=program["program_digest"],
            execution_digest=intent["commit_execution_digest"],
            binding_digest=preview["binding_digest"],
            document_revision_before=program["expected_document_revision"],
            duplicate=duplicate,
            job_uri=f"cad://jobs/{job['job_id']}" if job else None,
            admission_status=(
                "approval_required"
                if job is None
                else "released"
                if state == "queued"
                else "current_job"
            ),
            intent_id=intent["intent_id"],
            consent_id=consent["consent_id"] if consent else None,
            required_assurance=intent["required_assurance"],
            intent_uri=f"cad://intents/{intent['intent_id']}",
            consent_uri=(
                f"cad://consents/{consent['consent_id']}" if consent else None
            ),
        )

    @staticmethod
    def _rollback_preview_output(
        plan: dict[str, Any], *, duplicate: bool
    ) -> CadPreviewRollbackOutput:
        return CadPreviewRollbackOutput(
            rollback_plan_id=plan["plan_id"],
            checkpoint_id=plan["checkpoint_id"],
            original_receipt_id=plan["original_receipt_id"],
            eligible=plan["eligible"],
            conflicts=plan["conflicts"],
            current_document_revision=plan["current_document_revision"],
            expires_at=plan["expires_at"],
            duplicate=duplicate,
            resource_uri=f"cad://rollbacks/{plan['plan_id']}",
        )

    @staticmethod
    def _rollback_commit_output(
        plan: dict[str, Any],
        *,
        state: str,
        duplicate: bool,
        intent: dict[str, Any] | None = None,
        consent: dict[str, Any] | None = None,
        job: dict[str, Any] | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> CadCommitRollbackOutput:
        return CadCommitRollbackOutput(
            rollback_plan_id=plan["plan_id"],
            checkpoint_id=plan["checkpoint_id"],
            state=state,
            duplicate=duplicate,
            intent_id=intent["intent_id"] if intent else None,
            consent_id=consent["consent_id"] if consent else None,
            job_id=job["job_id"] if job else None,
            rollback_receipt_id=(
                receipt["rollback_receipt_id"] if receipt else None
            ),
            intent_uri=(
                f"cad://intents/{intent['intent_id']}" if intent else None
            ),
            consent_uri=(
                f"cad://consents/{consent['consent_id']}" if consent else None
            ),
            job_uri=f"cad://jobs/{job['job_id']}" if job else None,
            resource_uri=(
                f"cad://rollback-receipts/{receipt['rollback_receipt_id']}"
                if receipt
                else f"cad://rollbacks/{plan['plan_id']}"
            ),
        )

    def _require_phase7(self) -> None:
        if not self.policy.phase7_c2_enabled or self.policy.profile != "phase7_c2":
            raise GatewayError("feature_disabled")

    def _require_rollback(self) -> None:
        self._require_phase7()
        if not self.policy.public_rollback_enabled:
            raise GatewayError("feature_disabled")

    @staticmethod
    def _bounded(value: Any) -> str:
        if value is None:
            raise GatewayError("not_found")
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise GatewayError("response_too_large")
        return encoded

    @staticmethod
    def _repository_code(code: str) -> str:
        return {
            "cas_conflict": "version_conflict",
            "consent_expired": "consent_expired",
            "intent_expired": "intent_expired",
            "consent_not_approved": "consent_not_approved",
            "document_write_busy": "document_write_busy",
            "job_preexists_release": "idempotency_conflict",
            "intent_release_conflict": "idempotency_conflict",
            "intent_conflict": "idempotency_conflict",
            "consent_conflict": "idempotency_conflict",
            "rollback_plan_conflict": "idempotency_conflict",
        }.get(code, code)
