"""Typed Phase 6 CAD Program execution through the exact Managed Host."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Protocol

from autocad_contracts import (
    ProgramCommandMessage,
    ProgramCommitResult,
    ProgramPreviewResult,
    ProgramValidateResult,
    program_command_payload,
    program_command_payload_hash,
)

from .executor import AgentExecutionError


class ProgramRuntimeBroker(Protocol):
    async def select_write_runtime(
        self,
        binding: Any,
        *,
        required_capability: str,
        write_lock_enabled: bool,
        write_required: bool = True,
    ) -> Any: ...


class ProgramCommandExecutor:
    """No AutoCAD API access: broker selection and a narrow Host port only."""

    _CAPABILITIES = {
        "program_preview": "cad.program.preview",
        "program_commit": "cad.program.commit",
        "program_validate": "cad.program.validate",
    }

    def __init__(self, runtime_broker: ProgramRuntimeBroker) -> None:
        self._runtime_broker = runtime_broker
        self._document_mutexes: dict[str, asyncio.Lock] = {}

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
        health = await adapter.health()
        if not health.ok:
            raise AgentExecutionError(health.error_code or "managed_host_unavailable")
        self._validate_document_binding(health.payload, command)

        if command.effect_class != "write":
            return await self._execute_host(adapter, command)

        lock = self._document_mutexes.setdefault(
            command.binding.document_id,
            asyncio.Lock(),
        )
        if lock.locked():
            raise AgentExecutionError("agent_busy")
        async with lock:
            return await self._execute_host(adapter, command)

    async def _execute_host(
        self,
        adapter: Any,
        command: ProgramCommandMessage,
    ) -> dict[str, Any]:
        result = await adapter.program_command(
            command.kind,
            arguments=self._host_arguments(command),
            deadline_at=command.deadline_at,
        )
        if not result.ok or not isinstance(result.payload, dict):
            raise AgentExecutionError(
                result.error_code or "managed_host_unavailable"
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
    def _host_arguments(command: ProgramCommandMessage) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "execution_binding": command.binding.model_dump(mode="json"),
        }
        if command.program is not None:
            arguments["program"] = command.program.model_dump(
                mode="json",
                exclude_none=True,
            )
        if command.kind == "program_commit":
            arguments["preview_binding"] = {
                "preview_id": command.preview_id,
                "preview_digest": command.preview_digest,
            }
        elif command.validation is not None:
            arguments["validation"] = command.validation.model_dump(
                mode="json",
                exclude_none=True,
            )
        return arguments
