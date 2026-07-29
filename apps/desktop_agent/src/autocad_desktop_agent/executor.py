"""Narrow read-only AutoCAD executor used by the C1 command router."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath, PureWindowsPath
from typing import Any, Protocol

from autocad_contracts import CommandMessage, canonical_json


class CadReadPort(Protocol):
    async def health(self) -> Any: ...
    async def drawing_info(self) -> Any: ...


class ReadRuntimeBroker(Protocol):
    async def select_read_runtime(self) -> Any: ...


class SafeFileIPCCadReadPort:
    """The only adapter allowed to hold the write-capable legacy backend."""

    def __init__(self) -> None:
        from autocad_mcp.backends.safe_file_ipc import SafeFileIPCBackend

        self.__backend = SafeFileIPCBackend(allow_execute_lisp=False)

    async def health(self) -> Any:
        return await self.__backend.health()

    async def drawing_info(self) -> Any:
        return await self.__backend.drawing_info()


class AgentExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


SAFE_BACKEND_ERRORS = frozenset(
    {
        "autocad_not_running",
        "no_active_document",
        "autocad_busy",
        "modal_dialog_active",
        "active_document_changed",
        "dispatcher_timeout",
        "dispatcher_not_loaded",
        "command_routing_failed",
        "ipc_result_invalid",
        "managed_host_unavailable",
        "host_not_loaded",
        "protocol_mismatch",
        "runtime_version_mismatch",
        "session_rejected",
    }
)


@dataclass(frozen=True)
class CadPresence:
    runtime_state: str
    autocad_state: str
    document_name: str | None = None
    safe_error_code: str | None = None
    active_document_id: str | None = None
    active_document_revision: str | None = None
    product: str | None = None
    edition: str | None = None
    release_year: int | None = None
    series: str | None = None
    runtime_id: str | None = None
    runtime_role: str | None = None
    host_family: str | None = None
    host_version: str | None = None
    host_package_version: str | None = None
    host_package_hash: str | None = None
    host_handshake_state: str | None = None
    degradation_reason: str | None = None
    capability_manifest_hash: str | None = None
    registry_version: str | None = None


class ReadCommandExecutor:
    def __init__(
        self,
        port: CadReadPort,
        package: dict[str, str],
        agent_version: str,
        *,
        runtime_broker: ReadRuntimeBroker | None = None,
    ) -> None:
        self._port = port
        self._runtime_broker = runtime_broker
        self.package = dict(package)
        self.agent_version = agent_version

    def set_runtime_broker(self, runtime_broker: ReadRuntimeBroker) -> None:
        """Add the Phase 5 seam without changing the established constructor."""

        self._runtime_broker = runtime_broker

    async def _select_port(self) -> tuple[CadReadPort, Any | None]:
        if self._runtime_broker is None:
            return self._port, None
        selection = await self._runtime_broker.select_read_runtime()
        return selection.adapter, selection

    def validate_command(self, command: CommandMessage) -> None:
        if command.kind != "observe" or command.effect_class != "read":
            raise AgentExecutionError("capability_missing")
        if command.payload.get("observation_level") not in {"summary", "detail"}:
            raise AgentExecutionError("capability_missing")
        if command.payload.get("include_preview_image") is not False:
            raise AgentExecutionError("capability_missing")
        if command.payload.get("package") != self.package:
            raise AgentExecutionError("package_mismatch")
        if command.deadline_at is not None:
            deadline = datetime.fromisoformat(command.deadline_at.replace("Z", "+00:00"))
            if deadline <= datetime.now(timezone.utc):
                raise AgentExecutionError("deadline_expired")

    async def probe(self) -> CadPresence:
        try:
            port, selection = await self._select_port()
        except Exception as error:
            code = getattr(error, "code", "runtime_unavailable")
            state = (
                "runtime_version_mismatch"
                if code == "runtime_version_mismatch"
                else "plugin_required"
            )
            return CadPresence(
                state,
                "Chưa sẵn sàng",
                safe_error_code=code,
                runtime_id="managed_dotnet",
                runtime_role="primary",
                host_handshake_state="failed",
                degradation_reason=code,
            )
        result = await port.health()
        details = result.payload if result.ok else getattr(result, "details", None)
        details = details if isinstance(details, dict) else {}
        raw_document = details.get("active_document")
        document_name = (
            PureWindowsPath(raw_document).name
            if isinstance(raw_document, str) and raw_document
            else None
        )
        runtime = getattr(selection, "evidence", None)
        probe = getattr(selection, "probe", None)
        manifest = getattr(selection, "manifest", None)
        runtime_fields = {
            "active_document_id": (
                str(
                    details.get(
                        "active_document_id",
                        details.get("document_id"),
                    )
                )
                if details.get(
                    "active_document_id",
                    details.get("document_id"),
                )
                is not None
                else None
            ),
            "active_document_revision": (
                str(
                    details.get(
                        "active_document_revision",
                        details.get("document_revision"),
                    )
                )
                if details.get(
                    "active_document_revision",
                    details.get("document_revision"),
                )
                is not None
                else None
            ),
            "product": getattr(probe, "product", None),
            "edition": getattr(probe, "edition", None),
            "release_year": getattr(probe, "release_year", None),
            "series": getattr(probe, "series", None),
            "runtime_id": getattr(runtime, "id", None),
            "runtime_role": getattr(runtime, "role", None),
            "host_family": getattr(runtime, "host_family", None),
            "host_version": getattr(runtime, "host_version", None),
            "host_package_version": getattr(runtime, "package_version", None),
            "host_package_hash": getattr(runtime, "package_hash", None),
            "host_handshake_state": (
                "connected" if getattr(runtime, "id", None) == "managed_dotnet" else "not_applicable"
            ),
            "degradation_reason": getattr(selection, "degradation_reason", None),
            "capability_manifest_hash": (
                self._manifest_hash(manifest) if manifest is not None else None
            ),
            "registry_version": getattr(manifest, "registry_version", None),
        }
        if result.ok:
            state = (
                "degraded_compatibility"
                if getattr(selection, "degraded", False)
                else "online_idle"
            )
            return CadPresence(state, "Đã kết nối", document_name, **runtime_fields)
        code = self._safe_code(result.error_code)
        states = {
            "autocad_not_running": ("autocad_closed", "Chưa mở"),
            "no_active_document": ("no_document", "Đã kết nối"),
            "autocad_busy": ("online_busy_user", "Đang bận"),
            "modal_dialog_active": ("modal_dialog", "Đang chờ hộp thoại"),
            "dispatcher_timeout": ("incompatible", "Package chưa sẵn sàng"),
            "dispatcher_not_loaded": ("incompatible", "Package chưa sẵn sàng"),
        }
        runtime_state, autocad_state = states.get(code, ("incompatible", "Không tương thích"))
        return CadPresence(
            runtime_state,
            autocad_state,
            document_name,
            code,
            **runtime_fields,
        )

    async def execute(self, command: CommandMessage) -> dict[str, Any]:
        self.validate_command(command)
        try:
            port, selection = await self._select_port()
        except Exception as error:
            raise AgentExecutionError(getattr(error, "code", "backend_error")) from error
        detail = command.payload["observation_level"] == "detail"
        entity_snapshot = getattr(port, "entity_snapshot", None)
        if detail and not callable(entity_snapshot):
            raise AgentExecutionError("capability_missing")
        health = await port.health()
        if not health.ok:
            raise AgentExecutionError(self._safe_code(health.error_code))
        result = await port.drawing_info()
        if not result.ok or not isinstance(result.payload, dict):
            raise AgentExecutionError(self._safe_code(result.error_code))
        runtime = getattr(selection, "evidence", None)
        managed_dotnet = getattr(runtime, "id", None) == "managed_dotnet"
        summary = self._validate_summary(
            result.payload,
            require_compatibility_package=not managed_dotnet,
        )
        detail_payload: dict[str, Any] | None = None
        if detail:
            summary_revision = None
            if managed_dotnet:
                revision = result.payload.get("revision")
                if (
                    not isinstance(revision, dict)
                    or isinstance(revision.get("revision"), bool)
                    or not isinstance(revision.get("revision"), int)
                    or revision["revision"] <= 0
                ):
                    raise AgentExecutionError("protocol_mismatch")
                summary_revision = revision["revision"]
            detail_result = (
                await entity_snapshot(expected_revision=summary_revision)
                if summary_revision is not None
                else await entity_snapshot()
            )
            if not detail_result.ok or not isinstance(detail_result.payload, dict):
                raise AgentExecutionError(self._safe_code(detail_result.error_code))
            detail_payload = detail_result.payload
            if (
                summary_revision is not None
                and detail_payload.get("revision", {}).get("revision")
                != summary_revision
            ):
                raise AgentExecutionError("active_document_changed")
        revision_source = {
            "document_name": summary["document_name"],
            "entity_count": summary["entity_count"],
            "layers": summary["layers"],
            "layer_count": summary["layer_count"],
            "truncated": summary["truncated"],
        }
        revision = (
            str(detail_payload["revision"]["revision"])
            if detail_payload is not None
            else hashlib.sha256(
                canonical_json(revision_source).encode("utf-8")
            ).hexdigest()
        )
        entities = (
            [
                {
                    "entity_id": str(entity["handle"]),
                    "entity_type": str(entity["type"]).upper(),
                    "layer": str(entity["layer"]),
                    "space": str(entity.get("space", "model")),
                    "bounds": entity.get("bounds"),
                    "geometry": entity.get("geometry"),
                    "geometry_truncated": bool(
                        entity.get("geometry_truncated", False)
                    ),
                    "fingerprint": str(entity["fingerprint"]),
                }
                for entity in detail_payload["entities"]
            ]
            if detail_payload is not None
            else []
        )
        snapshot = {
            "snapshot_id": f"snapshot-{command.command_id}",
            "document_revision": revision,
            "observation_level": command.payload["observation_level"],
            "drawing": {
                **summary,
                **(
                    {
                        "document_id": detail_payload["document_id"],
                        "database_fingerprint": detail_payload[
                            "database_fingerprint"
                        ],
                    }
                    if detail_payload is not None
                    else {}
                ),
            },
            "entity_summary": {
                "entity_count": (
                    len(entities) if detail_payload is not None
                    else summary["entity_count"]
                ),
                "detail_available": detail_payload is not None,
                **(
                    {"truncated": bool(detail_payload.get("scan_truncated"))}
                    if detail_payload is not None
                    else {}
                ),
            },
            "entities": entities,
            "revision_evidence": {
                "revision_schema": "cad.revision/1",
                "revision_strength": (
                    "database_object_fingerprint"
                    if detail_payload is not None
                    else "summary_only"
                ),
                "commit_safe": (
                    detail_payload is not None
                    and not bool(detail_payload.get("scan_truncated"))
                ),
            },
        }
        return {
            "snapshot": snapshot,
            "execution_evidence": {
                "agent_version": self.agent_version,
                "runtime_state": "online_idle",
                "package": self.package,
                **(
                    {
                        "runtime": runtime.model_dump(mode="json", exclude_none=True),
                        "degraded": bool(getattr(selection, "degraded", False)),
                        "degradation_reason": getattr(
                            selection, "degradation_reason", None
                        ),
                    }
                    if runtime is not None
                    else {}
                ),
            },
        }

    def _validate_summary(
        self,
        value: dict[str, Any],
        *,
        require_compatibility_package: bool = True,
    ) -> dict[str, Any]:
        raw_name = value.get("document_name")
        layers = value.get("layers")
        if not isinstance(raw_name, str) or not raw_name or not isinstance(layers, list):
            raise AgentExecutionError("ipc_result_invalid")
        document_name = PureWindowsPath(raw_name).name or PurePath(raw_name).name
        if not document_name or len(document_name) > 255:
            raise AgentExecutionError("ipc_result_invalid")
        if len(layers) > 256 or any(not isinstance(item, str) or len(item) > 255 for item in layers):
            raise AgentExecutionError("ipc_result_invalid")
        entity_count = value.get("entity_count")
        layer_count = value.get("layer_count")
        if not isinstance(entity_count, int) or entity_count < 0:
            raise AgentExecutionError("ipc_result_invalid")
        if not isinstance(layer_count, int) or layer_count < len(layers):
            raise AgentExecutionError("ipc_result_invalid")
        if require_compatibility_package:
            if value.get("dispatcher_version") != self.package["version"]:
                raise AgentExecutionError("package_mismatch")
            if value.get("package_id") != self.package["package_id"]:
                raise AgentExecutionError("package_mismatch")
            if value.get("package_version") != self.package["version"]:
                raise AgentExecutionError("package_mismatch")
        summary = {
            "document_name": document_name,
            "entity_count": entity_count,
            "layers": layers,
            "layer_count": layer_count,
            "truncated": bool(value.get("truncated")),
        }
        if require_compatibility_package:
            summary.update(
                dispatcher_version=value["dispatcher_version"],
                package_id=value["package_id"],
                package_version=value["package_version"],
            )
        return summary

    @staticmethod
    def _manifest_hash(manifest: Any) -> str:
        from autocad_contracts import canonical_capability_manifest_hash

        return canonical_capability_manifest_hash(manifest)

    @staticmethod
    def _safe_code(code: str | None) -> str:
        normalized = {
            "dispatcher_missing_in_active_document": "dispatcher_not_loaded",
        }.get(code, code)
        return normalized if normalized in SAFE_BACKEND_ERRORS else "backend_error"


# Compatibility name retained for Phase 3/4/5 callers and packaging.
DrawingInfoExecutor = ReadCommandExecutor
