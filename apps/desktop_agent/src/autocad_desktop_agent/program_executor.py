"""Typed Phase 6 CAD Program execution through the exact Managed Host."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol

from autocad_contracts import (
    ExecutionBindingV1,
    ProgramCommandMessage,
    ProgramCommitResult,
    ProgramPreviewResult,
    ProgramValidateResult,
    RollbackCommandMessage,
    program_command_payload,
    program_command_payload_hash,
    rollback_command_payload,
    rollback_command_payload_hash,
)

from .executor import AgentExecutionError
from .phase8_admission import Phase8PlanAdmission, VerifiedPhase8Plan


class ProgramRuntimeBroker(Protocol):
    async def select_write_runtime(
        self,
        binding: Any,
        *,
        required_capability: str,
        write_lock_enabled: bool,
        write_required: bool = True,
    ) -> Any: ...


class DocumentWriteSerializer:
    """One fail-fast write lane per document across program and rollback flows."""

    def __init__(self) -> None:
        self._mutexes: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, document_id: str) -> AsyncIterator[None]:
        lock = self._mutexes.setdefault(document_id, asyncio.Lock())
        if lock.locked():
            raise AgentExecutionError("agent_busy")
        async with lock:
            yield


class ProgramCommandExecutor:
    """No AutoCAD API access: broker selection and a narrow Host port only."""

    _CAPABILITIES = {
        "program_preview": "cad.program.preview",
        "program_commit": "cad.program.commit",
        "program_validate": "cad.program.validate",
    }

    def __init__(
        self,
        runtime_broker: ProgramRuntimeBroker,
        *,
        write_serializer: DocumentWriteSerializer | None = None,
        phase8_admission: Phase8PlanAdmission | None = None,
    ) -> None:
        self._runtime_broker = runtime_broker
        self.write_serializer = write_serializer or DocumentWriteSerializer()
        self._phase8_admission = phase8_admission

    def validate_command(self, command: ProgramCommandMessage) -> None:
        if not isinstance(command, ProgramCommandMessage):
            raise AgentExecutionError("capability_missing")
        if program_command_payload_hash(command) != command.payload_hash:
            raise AgentExecutionError("payload_mismatch")
        if command.deadline_at is None:
            raise AgentExecutionError("deadline_expired")
        deadline = datetime.fromisoformat(command.deadline_at.replace("Z", "+00:00"))
        if deadline <= datetime.now(timezone.utc):
            raise AgentExecutionError("deadline_expired")
        if command.binding.runtime_id != "managed_dotnet":
            raise AgentExecutionError("runtime_mismatch")
        if (
            command.binding.host_family != "R25"
            or command.binding.runtime_role != "primary"
        ):
            raise AgentExecutionError("runtime_mismatch")

    async def execute(
        self,
        command: ProgramCommandMessage,
        *,
        write_lock_enabled: bool,
    ) -> dict[str, Any]:
        self.validate_command(command)
        try:
            selection = await self._runtime_broker.select_write_runtime(
                command.binding,
                required_capability=self._CAPABILITIES[command.kind],
                write_lock_enabled=write_lock_enabled,
                write_required=command.effect_class == "write",
            )
        except Exception as error:
            raise AgentExecutionError(
                getattr(error, "code", "managed_host_unavailable")
            ) from error

        adapter = selection.adapter
        phase8 = self._verify_phase8(command, selection)
        health = await adapter.health()
        if not health.ok:
            raise AgentExecutionError(health.error_code or "managed_host_unavailable")
        self._validate_document_binding(health.payload, command)

        if command.effect_class != "write":
            return await self._execute_host(adapter, command, phase8)

        async with self.write_serializer.acquire(command.binding.document_id):
            return await self._execute_host(adapter, command, phase8)

    async def _execute_host(
        self,
        adapter: Any,
        command: ProgramCommandMessage,
        phase8: VerifiedPhase8Plan | None = None,
    ) -> dict[str, Any]:
        result = await adapter.program_command(
            command.kind,
            arguments=self._host_arguments(command, phase8),
            deadline_at=command.deadline_at,
        )
        if not result.ok or not isinstance(result.payload, dict):
            raise AgentExecutionError(
                result.error_code or "managed_host_unavailable"
            )
        if phase8 is not None:
            if self._phase8_admission is None:
                raise AgentExecutionError("capability_missing")
            return self._phase8_admission.verify_result(
                phase8,
                result.payload,
                command_kind=command.kind,
            )
        model = {
            "program_preview": ProgramPreviewResult,
            "program_commit": ProgramCommitResult,
            "program_validate": ProgramValidateResult,
        }[command.kind].model_validate(result.payload, strict=True)
        return model.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _validate_document_binding(
        payload: dict[str, Any] | None,
        command: ProgramCommandMessage,
    ) -> None:
        if not isinstance(payload, dict):
            raise AgentExecutionError("binding_mismatch")
        document_id = payload.get("active_document_id", payload.get("document_id"))
        revision = payload.get(
            "active_document_revision",
            payload.get("document_revision"),
        )
        if document_id != command.binding.document_id:
            raise AgentExecutionError("active_document_changed")
        if str(revision) != command.binding.document_revision:
            raise AgentExecutionError("stale_snapshot")

    @staticmethod
    def _host_arguments(
        command: ProgramCommandMessage,
        phase8: VerifiedPhase8Plan | None = None,
    ) -> dict[str, Any]:
        if phase8 is not None:
            return phase8.host_arguments()
        arguments: dict[str, Any] = {
            "execution_binding": command.binding.model_dump(mode="json"),
        }
        if command.program is not None:
            arguments["program"] = command.program.model_dump(
                mode="json",
                exclude_none=True,
            )
        if command.kind == "program_preview":
            arguments["preview_id"] = command.preview_id
            arguments["expires_at"] = command.expires_at
        elif command.kind == "program_commit":
            arguments["preview_binding"] = {
                "preview_id": command.preview_id,
                "preview_digest": command.preview_digest,
            }
            arguments["receipt_id"] = command.receipt_id
        elif command.validation is not None:
            arguments["validation"] = command.validation.model_dump(
                mode="json",
                exclude_none=True,
            )
        return arguments

    def _verify_phase8(
        self,
        command: ProgramCommandMessage,
        selection: Any,
    ) -> VerifiedPhase8Plan | None:
        plan = command.execution_plan
        if plan is None:
            return None
        if self._phase8_admission is None:
            raise AgentExecutionError("capability_missing")
        if not isinstance(command.binding, ExecutionBindingV1):
            raise AgentExecutionError("binding_mismatch")
        return self._phase8_admission.verify(
            plan,
            binding=command.binding,
            command_kind=command.kind,
            approval_binding=command.approval_binding,
            capability_states=getattr(selection, "capability_states", {}),
            server_capability_evidence=command.capability_evidence,
            legacy_binding=None,
            device_id=command.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            issued_at=command.issued_at,
            preview_id=command.preview_id,
            preview_digest=command.preview_digest,
            preview_expires_at=command.expires_at,
            receipt_id=command.receipt_id,
            idempotency_key=command.idempotency_key,
        )


_ROLLBACK_KINDS = {
    "receipt_lookup": ("cad.recovery.receipt_query", "read"),
    "checkpoint_lookup": ("cad.rollback.checkpoint.lookup", "read"),
    "rollback_preview": ("cad.rollback.preview", "read"),
    "rollback_commit": ("cad.rollback.commit", "write"),
    "rollback_validate": ("cad.rollback.validate", "read"),
}

class RollbackCommandExecutor:
    """Narrow Phase 7 rollback port; it cannot issue Undo or accept entity handles."""

    def __init__(
        self,
        runtime_broker: ProgramRuntimeBroker,
        *,
        write_serializer: DocumentWriteSerializer | None = None,
    ) -> None:
        self._runtime_broker = runtime_broker
        self.write_serializer = write_serializer or DocumentWriteSerializer()

    def validate_command(
        self, command: RollbackCommandMessage | dict[str, Any]
    ) -> RollbackCommandMessage:
        try:
            parsed = (
                command
                if isinstance(command, RollbackCommandMessage)
                else RollbackCommandMessage.model_validate(command)
            )
        except (TypeError, ValueError) as error:
            raise AgentExecutionError("payload_mismatch") from error
        if rollback_command_payload_hash(parsed) != parsed.payload_hash:
            raise AgentExecutionError("payload_mismatch")
        try:
            deadline = datetime.fromisoformat(
                str(parsed.deadline_at).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as error:
            raise AgentExecutionError("deadline_expired") from error
        if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
            raise AgentExecutionError("deadline_expired")
        if (
            parsed.binding.runtime_id != "managed_dotnet"
            or parsed.binding.runtime_role != "primary"
            or parsed.binding.host_family != "R25"
        ):
            raise AgentExecutionError("runtime_mismatch")
        return parsed

    async def execute(
        self,
        command: RollbackCommandMessage | dict[str, Any],
        *,
        write_lock_enabled: bool,
    ) -> dict[str, Any]:
        parsed = self.validate_command(command)
        kind = parsed.kind
        capability, effect_class = _ROLLBACK_KINDS[kind]
        binding = parsed.binding
        try:
            selection = await self._runtime_broker.select_write_runtime(
                binding,
                required_capability=capability,
                write_lock_enabled=write_lock_enabled,
                write_required=effect_class == "write",
            )
        except Exception as error:
            raise AgentExecutionError(
                getattr(error, "code", "managed_host_unavailable")
            ) from error
        adapter = selection.adapter
        health = await adapter.health()
        if not health.ok or not isinstance(health.payload, dict):
            raise AgentExecutionError(
                health.error_code or "managed_host_unavailable"
            )
        if health.payload.get("active_document_id") != binding.document_id:
            raise AgentExecutionError("active_document_changed")
        if str(health.payload.get("active_document_revision")) != str(
            binding.document_revision
        ):
            raise AgentExecutionError("stale_snapshot")

        if effect_class == "read":
            return await self._dispatch(adapter, parsed)
        async with self.write_serializer.acquire(binding.document_id):
            return await self._dispatch(adapter, parsed)

    @staticmethod
    async def _dispatch(
        adapter: Any,
        command: RollbackCommandMessage,
    ) -> dict[str, Any]:
        result = await adapter.program_command(
            command.kind,
            arguments={
                **command.arguments.model_dump(mode="json"),
                "execution_binding": command.binding.model_dump(mode="json"),
            },
            deadline_at=command.deadline_at,
        )
        if not result.ok or not isinstance(result.payload, dict):
            raise AgentExecutionError(
                result.error_code or "managed_host_unavailable"
            )
        return dict(result.payload)
