"""Bounded ``cad.host/1`` client for the local Managed .NET read host."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from autocad_contracts import (
    CapabilityManifest,
    canonical_json,
    operation_registry_digest,
)

from .contracts import RuntimeProbe

PROTOCOL = "cad.host/1"
MAX_FRAME_BYTES = 1_048_576
MAX_PHASE10_SNAPSHOT_ENTITIES = 5_000


@dataclass(frozen=True)
class CadPortResult:
    ok: bool
    payload: dict[str, Any] | None = None
    error_code: str | None = None
    details: dict[str, Any] | None = None


class HostTransport(Protocol):
    async def request(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


class NamedPipeJsonTransport:
    """One authenticated Host session over a current-user Named Pipe."""

    def __init__(
        self,
        pipe_name: str,
        *,
        timeout_seconds: float = 10,
        stream_factory: Callable[[], Any] | None = None,
    ) -> None:
        if stream_factory is None and os.name != "nt":
            raise OSError("Managed Host Named Pipe is only available on Windows")
        if not pipe_name or "\\" in pipe_name or "/" in pipe_name:
            raise ValueError("pipe_name must be a logical local name")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._path = rf"\\.\pipe\{pipe_name}"
        self._timeout_seconds = timeout_seconds
        self._stream_factory = stream_factory or (
            lambda: open(self._path, "r+b", buffering=0)  # noqa: SIM115
        )
        self._stream: Any | None = None
        self._lock = asyncio.Lock()
        self._stream_lock = threading.Lock()

    async def request(self, envelope: dict[str, Any]) -> dict[str, Any]:
        body = canonical_json(envelope).encode("utf-8")
        if len(body) > MAX_FRAME_BYTES:
            raise ValueError("cad.host frame exceeds the bounded limit")
        timeout = self._request_timeout(envelope)
        if timeout <= 0:
            raise TimeoutError("managed_host_unavailable")
        async with self._lock:
            result: concurrent.futures.Future[dict[str, Any]] = (
                concurrent.futures.Future()
            )
            cancelled = threading.Event()
            worker = threading.Thread(
                target=self._run_request,
                args=(body, result, cancelled),
                name="autocad-managed-host-io",
                daemon=True,
            )
            worker.start()
            try:
                return await asyncio.wait_for(
                    asyncio.wrap_future(result),
                    timeout=timeout,
                )
            except (TimeoutError, asyncio.CancelledError):
                cancelled.set()
                self._abort_stream()
                raise

    def _request_sync(
        self,
        body: bytes,
        cancelled: threading.Event,
    ) -> dict[str, Any]:
        with self._stream_lock:
            stream = self._stream
        if stream is None:
            stream = self._stream_factory()
            with self._stream_lock:
                if cancelled.is_set():
                    stream.close()
                    raise TimeoutError("managed_host_unavailable")
                if self._stream is None:
                    self._stream = stream
                else:
                    replacement, stream = stream, self._stream
                    replacement.close()
        try:
            stream.write(struct.pack("<I", len(body)) + body)
            size = struct.unpack("<I", self._read_exact(stream, 4))[0]
            if size <= 0 or size > MAX_FRAME_BYTES:
                raise ValueError("cad.host response frame is invalid")
            value = json.loads(self._read_exact(stream, size).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("cad.host response must be an object")
            return value
        except Exception:
            self._discard_stream(stream)
            raise

    def _run_request(
        self,
        body: bytes,
        result: concurrent.futures.Future[dict[str, Any]],
        cancelled: threading.Event,
    ) -> None:
        try:
            value = self._request_sync(body, cancelled)
        except Exception as error:  # noqa: BLE001
            if not result.done():
                result.set_exception(error)
        else:
            if not result.done():
                result.set_result(value)

    @staticmethod
    def _read_exact(stream: Any, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = stream.read(size - len(chunks))
            if not chunk:
                raise EOFError("Managed Host disconnected")
            chunks.extend(chunk)
        return bytes(chunks)

    def _request_timeout(self, envelope: dict[str, Any]) -> float:
        deadline = envelope.get("deadline_at")
        if not isinstance(deadline, str):
            return self._timeout_seconds
        try:
            parsed = datetime.fromisoformat(deadline)
        except ValueError:
            return self._timeout_seconds
        if parsed.tzinfo is None:
            return self._timeout_seconds
        remaining = parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)
        return min(self._timeout_seconds, remaining.total_seconds())

    def _abort_stream(self) -> None:
        with self._stream_lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return
        threading.Thread(
            target=stream.close,
            name="autocad-managed-host-abort",
            daemon=True,
        ).start()

    def close(self) -> None:
        with self._stream_lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()

    def _discard_stream(self, stream: Any) -> None:
        with self._stream_lock:
            if self._stream is stream:
                self._stream = None
        stream.close()


class ManagedDotNetCadReadPort:
    """Read-only adapter. It has no raw command, path, assembly, or write API."""

    runtime_id = "managed_dotnet"

    def __init__(
        self,
        transport: HostTransport,
        *,
        session_secret: bytes,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> None:
        if len(session_secret) < 32:
            raise ValueError("Managed Host session secret is too short")
        self._transport = transport
        self._secret = bytes(session_secret)
        self._agent_version = agent_version
        self._expected_host_family = expected_host_family
        self._session_id = f"agent-{uuid.uuid4().hex}"
        self._sequence = 0
        self._handshake: dict[str, Any] | None = None
        self._handshake_error: str | None = None

    @classmethod
    def from_bootstrap(
        cls,
        bootstrap_path: str | Path,
        *,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> "ManagedDotNetCadReadPort":
        value = json.loads(Path(bootstrap_path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("protocol_version") != PROTOCOL:
            raise ValueError("Managed Host bootstrap protocol is invalid")
        pipe_name = value.get("pipe_name")
        encoded_secret = value.get("session_secret_base64")
        if not isinstance(pipe_name, str) or not isinstance(encoded_secret, str):
            raise ValueError("Managed Host bootstrap is incomplete")
        secret = base64.b64decode(encoded_secret, validate=True)
        return cls(
            NamedPipeJsonTransport(pipe_name),
            session_secret=secret,
            agent_version=agent_version,
            expected_host_family=expected_host_family,
        )

    @classmethod
    def from_default_bootstrap(
        cls,
        *,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> "ManagedDotNetCadReadPort":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise OSError("LOCALAPPDATA is unavailable")
        return cls.from_bootstrap(
            Path(local)
            / "KythuatVang"
            / "AutoCADMcp"
            / "managed-host-r25.json",
            agent_version=agent_version,
            expected_host_family=expected_host_family,
        )

    async def probe(self) -> RuntimeProbe:
        try:
            handshake = await self._ensure_handshake()
        except Exception as error:
            self._handshake_error = self._safe_error(error)
            return RuntimeProbe(
                runtime_id=self.runtime_id,
                available=False,
                reason=self._handshake_error,
            )
        return RuntimeProbe(
            runtime_id=self.runtime_id,
            available=True,
            product=handshake["product"],
            edition=handshake["edition"],
            release_year=handshake["release_year"],
            series=handshake.get("series"),
            active_document=handshake.get("active_document_id"),
        )

    async def health(self) -> CadPortResult:
        try:
            handshake = await self._ensure_handshake()
            result = await self._command("host.health", arguments={})
            status = result.get("status")
            status_errors = {
                "no_document": "no_active_document",
                "busy": "autocad_busy",
                "modal_dialog": "modal_dialog_active",
            }
            if status != "ready" and status not in status_errors:
                raise RuntimeError("protocol_mismatch")
        except Exception as error:
            code = self._safe_error(error)
            self._handshake = None
            return CadPortResult(False, error_code=code, details={"handshake_state": "failed"})
        details = dict(result)
        details.setdefault("product", handshake["product"])
        details.setdefault("edition", handshake["edition"])
        details.setdefault("release_year", handshake["release_year"])
        details.setdefault("series", handshake.get("series"))
        details.setdefault(
            "active_document",
            result.get("active_document_name") or result.get("document_name"),
        )
        details["handshake_state"] = "connected"
        if status != "ready":
            return CadPortResult(
                False,
                error_code=status_errors[status],
                details=details,
            )
        return CadPortResult(True, payload=details)

    async def drawing_info(self) -> CadPortResult:
        try:
            handshake = await self._ensure_handshake()
            result = await self._command(
                "drawing.observe.summary",
                document_id=handshake.get("active_document_id"),
                arguments={"include_layers": True, "max_layers": 256},
            )
            value = dict(result)
            layers_truncated = value.get("layers_truncated")
            if not isinstance(layers_truncated, bool):
                raise RuntimeError("protocol_mismatch")
            if "truncated" in value and value["truncated"] != layers_truncated:
                raise RuntimeError("protocol_mismatch")
            value["truncated"] = layers_truncated
            value.pop("layers_truncated")
            value.setdefault("layers", [])
            value.setdefault("layer_count", len(value["layers"]))
        except Exception as error:
            code = self._safe_error(error)
            if code in {"managed_host_unavailable", "session_rejected"}:
                self._handshake = None
            return CadPortResult(False, error_code=code)
        return CadPortResult(True, payload=value)

    async def entity_snapshot(
        self,
        *,
        limit: int = 512,
        expected_revision: int | None = None,
    ) -> CadPortResult:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_PHASE10_SNAPSHOT_ENTITIES
        ):
            return CadPortResult(False, error_code="capability_missing")
        try:
            handshake = await self._ensure_handshake()
            entities: list[dict[str, Any]] = []
            cursor = 0
            revision: dict[str, Any] | None = None
            document_id = handshake.get("active_document_id")
            while len(entities) < limit:
                arguments: dict[str, Any] = {
                    "cursor": cursor,
                    "limit": min(200, limit - len(entities)),
                    "max_scan": 20_000,
                    "space": "model",
                    "types": ["LINE", "CIRCLE", "LWPOLYLINE", "ARC"],
                }
                pinned_revision = (
                    revision["revision"]
                    if revision is not None
                    else expected_revision
                )
                if pinned_revision is not None:
                    arguments["expected_revision"] = pinned_revision
                page = await self._command(
                    "entity.snapshot.page",
                    document_id=document_id,
                    arguments=arguments,
                )
                page_revision = page.get("revision")
                page_entities = page.get("entities")
                if (
                    not isinstance(page_revision, dict)
                    or not isinstance(page_revision.get("revision"), int)
                    or not isinstance(page_entities, list)
                ):
                    raise RuntimeError("protocol_mismatch")
                if revision is None:
                    revision = page_revision
                elif page_revision["revision"] != revision["revision"]:
                    raise RuntimeError("active_document_changed")
                entities.extend(page_entities)
                next_cursor = page.get("next_cursor")
                if next_cursor is None:
                    return CadPortResult(
                        True,
                        payload={
                            **page,
                            "revision": revision,
                            "entities": entities,
                            "returned_count": len(entities),
                            "scan_truncated": False,
                        },
                    )
                if not isinstance(next_cursor, int) or next_cursor <= cursor:
                    raise RuntimeError("protocol_mismatch")
                cursor = next_cursor
            return CadPortResult(
                True,
                payload={
                    **page,
                    "revision": revision,
                    "entities": entities,
                    "returned_count": len(entities),
                    "next_cursor": cursor,
                    "scan_truncated": True,
                },
            )
        except Exception as error:
            code = self._safe_error(error)
            if code in {"managed_host_unavailable", "session_rejected"}:
                self._handshake = None
            return CadPortResult(False, error_code=code)

    def manifest(self, probe: RuntimeProbe) -> CapabilityManifest:
        if self._handshake is None:
            raise RuntimeError("managed_host_unavailable")
        handshake = self._handshake
        allowed = {
            "observe.summary",
            "entity.snapshot.v2",
            "entity.geometry.arc/1",
            "entity.geometry.circle/1",
            "entity.geometry.line/1",
            "entity.geometry.polyline/1",
            "cad.program.preview",
            "cad.program.commit",
            "cad.program.validate",
            "cad.recovery.receipt_query",
            "cad.rollback.checkpoint.lookup",
            "cad.rollback.preview",
            "cad.rollback.commit",
            "cad.rollback.validate",
        }
        capabilities = [
            capability
            for capability in handshake["capabilities"]
            if capability in allowed or self._phase8_capability_allowed(capability)
        ]
        registry_version, registry_hash = self._operation_registry_binding(
            handshake
        )
        return CapabilityManifest.model_validate(
            {
                "schema_version": "cad.capability/1",
                "registry_version": registry_version,
                "operation_registry_hash": registry_hash,
                "cad_products": [
                    {
                        "product": probe.product or handshake["product"],
                        "edition": "full",
                        "release_year": probe.release_year,
                        "series": probe.series,
                        "runtime": {
                            "id": self.runtime_id,
                            "role": "primary",
                            "host_family": handshake["host_family"],
                            "host_version": handshake["host_version"],
                            "framework": ".NET 8",
                            "package_id": handshake["package_id"],
                            "package_version": handshake["package_version"],
                            "package_hash": handshake["package_hash"],
                        },
                        "capabilities": capabilities,
                    }
                ],
            }
        )

    def phase8_capability_states(self) -> dict[str, str]:
        if self._handshake is None:
            return {}
        states = self._handshake.get("capability_states")
        if not isinstance(states, dict):
            return {}
        allowed_states = {
            "unsupported",
            "contract_only",
            "preview_only",
            "lab_commit",
            "certified",
        }
        advertised = set(self._handshake.get("capabilities", ()))
        return {
            key: state
            for key, state in states.items()
            if (
                isinstance(key, str)
                and isinstance(state, str)
                and key in advertised
                and state in allowed_states
                and self._phase8_capability_allowed(key)
            )
        }

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _operation_registry_binding(
        handshake: dict[str, Any],
    ) -> tuple[str, str]:
        evidence = handshake.get("phase8_host_evidence")
        if evidence is None:
            return "cad.program/0.2", operation_registry_digest()
        if not isinstance(evidence, dict):
            raise RuntimeError("protocol_mismatch")
        version = evidence.get("operation_registry_version")
        digest = evidence.get("operation_registry_hash")
        if (
            version != "cad.operation-registry/1"
            or not isinstance(digest, str)
            or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise RuntimeError("protocol_mismatch")
        return version, digest

    @staticmethod
    def _phase8_capability_allowed(capability: str) -> bool:
        denied = {
            "delete",
            "erase",
            "trim",
            "extend",
            "fillet",
            "chamfer",
            "join",
            "explode",
        }
        tokens = set(capability.split("."))
        if denied.intersection(tokens):
            return False
        if capability in {
            "cad.program.v1.preview",
            "cad.program.v1.commit",
            "cad.program.v1.compile",
            "cad.program.v1.validate",
        }:
            return True
        if capability.startswith("cad.op."):
            return bool(
                {
                    "copy",
                    "pattern",
                    "offset",
                    "mirror_copy",
                    "block_insert",
                    "annotation",
                    "move",
                    "rotate",
                    "scale",
                }.intersection(tokens)
                and {"line", "circle", "lwpolyline"}.intersection(tokens)
            )
        if capability.startswith("cad.rollback.checkpoint.v2."):
            return bool({"line", "circle", "lwpolyline"}.intersection(tokens))
        return capability in {
            "cad.validation.geometry.basic.v1",
            "cad.validation.document.revision.v1",
            "cad.validation.entity.fingerprint.v1",
            "cad.validation.transform.result.v1",
            "cad.validation.rollback.eligibility.v1",
        }

    async def program_command(
        self,
        kind: str,
        *,
        arguments: dict[str, Any],
        deadline_at: str | None,
    ) -> CadPortResult:
        operation_id = {
            "program_preview": "cad.program.preview",
            "program_commit": "cad.program.commit",
            "program_validate": "cad.program.validate",
            "receipt_lookup": "cad.recovery.receipt_query",
            "checkpoint_lookup": "cad.rollback.checkpoint.lookup",
            "rollback_preview": "cad.rollback.preview",
            "rollback_commit": "cad.rollback.commit",
            "rollback_validate": "cad.rollback.validate",
        }.get(kind)
        if operation_id is None:
            return CadPortResult(False, error_code="capability_missing")
        try:
            await self._ensure_handshake()
            host_arguments = dict(arguments)
            execution_binding = host_arguments.get("execution_binding")
            execution_plan = host_arguments.get("execution_plan")
            approval_binding = host_arguments.get("approval_binding")
            if kind not in {
                "program_preview",
                "program_commit",
                "program_validate",
            }:
                host_arguments.pop("execution_binding", None)
            document_id = (
                execution_binding.get("document_id")
                if isinstance(execution_binding, dict)
                else execution_plan.get("document_id")
                if isinstance(execution_plan, dict)
                else None
            )
            result = await self._command(
                operation_id,
                arguments=host_arguments,
                document_id=document_id,
                deadline_at=deadline_at,
                command_id=(
                    approval_binding.get("command_id")
                    if kind == "program_commit"
                    and isinstance(approval_binding, dict)
                    else None
                ),
            )
        except Exception as error:
            code = self._safe_error(error)
            if code in {"managed_host_unavailable", "session_rejected"}:
                self._handshake = None
            return CadPortResult(False, error_code=code)
        return CadPortResult(True, payload=result)

    async def _ensure_handshake(self) -> dict[str, Any]:
        if self._handshake is not None:
            return self._handshake
        nonce = secrets.token_hex(16)
        payload = {
            "session_nonce": nonce,
            "agent_version": self._agent_version,
            "protocol_min": PROTOCOL,
            "protocol_max": PROTOCOL,
        }
        response = await self._transport.request(self._envelope("handshake", payload))
        value = self._validate_response(response, expected_type="handshake_result")
        required = {
            "selected_protocol",
            "host_family",
            "host_version",
            "package_id",
            "package_version",
            "package_hash",
            "session_proof",
            "product",
            "edition",
            "release_year",
            "capabilities",
        }
        if not required.issubset(value) or value["selected_protocol"] != PROTOCOL:
            raise RuntimeError("protocol_mismatch")
        expected_proof = hmac.new(
            self._secret,
            f"{PROTOCOL}\n{self._session_id}\n{nonce}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(str(value["session_proof"]), expected_proof):
            raise RuntimeError("session_rejected")
        if value["edition"] != "full":
            raise RuntimeError("protocol_mismatch")
        if (
            self._expected_host_family is not None
            and value["host_family"] != self._expected_host_family
        ):
            raise RuntimeError("runtime_version_mismatch")
        capabilities = value["capabilities"]
        if not isinstance(capabilities, list) or "observe.summary" not in capabilities:
            raise RuntimeError("capability_missing")
        self._handshake = value
        return value

    async def _command(
        self,
        operation_id: str,
        *,
        arguments: dict[str, Any],
        document_id: str | None = None,
        deadline_at: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation_id": operation_id,
            "operation_version": 1,
            "arguments": arguments,
        }
        if document_id is not None:
            payload["document_id"] = document_id
        response = await self._transport.request(
            self._envelope(
                "command",
                payload,
                deadline_at=deadline_at,
                command_id=command_id,
            )
        )
        value = self._validate_response(response, expected_type="result")
        if value.get("operation_id") != operation_id:
            raise RuntimeError("protocol_mismatch")
        result = value.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("protocol_mismatch")
        runtime_evidence = value.get("runtime_evidence")
        if not isinstance(runtime_evidence, dict):
            raise RuntimeError("protocol_mismatch")
        handshake = self._handshake or {}
        if (
            runtime_evidence.get("runtime_id") != "managed_dotnet"
            or runtime_evidence.get("runtime_role") != "primary"
            or runtime_evidence.get("host_family") != handshake.get("host_family")
            or runtime_evidence.get("host_version") != handshake.get("host_version")
        ):
            raise RuntimeError("runtime_version_mismatch")
        if value.get("status") == "duplicate":
            result = dict(result)
            result["duplicate"] = True
        return result

    def _envelope(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        deadline_at: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        command_id = command_id or f"{message_type}-{uuid.uuid4().hex}"
        if deadline_at is None:
            deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
        else:
            deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        deadline = deadline.astimezone(timezone.utc)
        # .NET's round-trip "O" parser requires seven fractional-second
        # digits; Python's ISO formatter emits six.
        deadline_at = (
            deadline.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{deadline.microsecond:06d}0+00:00"
        )
        envelope = {
            "protocol_version": PROTOCOL,
            "message_type": message_type,
            "session_id": self._session_id,
            "command_id": command_id,
            "sequence": self._sequence,
            "deadline_at": deadline_at,
            "payload_hash": hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest(),
            "payload": payload,
        }
        self._sequence += 1
        return envelope

    def _validate_response(
        self,
        response: dict[str, Any],
        *,
        expected_type: str,
    ) -> dict[str, Any]:
        if response.get("protocol_version") != PROTOCOL:
            raise RuntimeError("protocol_mismatch")
        if response.get("session_id") != self._session_id:
            raise RuntimeError("session_rejected")
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("protocol_mismatch")
        expected_hash = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        if response.get("payload_hash") != expected_hash:
            raise RuntimeError("payload_mismatch")
        if response.get("message_type") == "error":
            raise RuntimeError(str(payload.get("error_code", "internal_error")))
        if response.get("message_type") != expected_type:
            raise RuntimeError("protocol_mismatch")
        return payload

    @staticmethod
    def _safe_error(error: Exception) -> str:
        code = str(error)
        allowed = {
            "protocol_mismatch",
            "session_rejected",
            "payload_mismatch",
            "deadline_expired",
            "runtime_version_mismatch",
            "capability_missing",
            "no_active_document",
            "autocad_busy",
            "modal_dialog_active",
            "active_document_changed",
            "document_changed",
            "stale_snapshot",
            "program_invalid",
            "preview_mismatch",
            "approval_binding_mismatch",
            "preview_required",
            "runtime_changed",
            "duplicate_payload_mismatch",
            "commit_validation_failed",
            "preview_abort_failed",
            "outcome_unknown",
            "ledger_corrupt",
            "ledger_full",
        }
        return code if code in allowed else "managed_host_unavailable"


class ReloadingManagedDotNetCadReadPort:
    """Discover and reload the current Managed Host bootstrap on demand."""

    runtime_id = "managed_dotnet"

    def __init__(
        self,
        bootstrap_path: str | Path,
        *,
        agent_version: str,
        expected_host_family: str | None = None,
        adapter_factory: Callable[[Path], ManagedDotNetCadReadPort] | None = None,
    ) -> None:
        self._bootstrap_path = Path(bootstrap_path)
        self._agent_version = agent_version
        self._expected_host_family = expected_host_family
        self._adapter_factory = adapter_factory or self._load_adapter
        self._adapter: ManagedDotNetCadReadPort | None = None
        self._bootstrap_hash: str | None = None

    @classmethod
    def from_default_bootstrap(
        cls,
        *,
        agent_version: str,
        expected_host_family: str | None = None,
    ) -> "ReloadingManagedDotNetCadReadPort":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise OSError("LOCALAPPDATA is unavailable")
        return cls(
            Path(local)
            / "KythuatVang"
            / "AutoCADMcp"
            / "managed-host-r25.json",
            agent_version=agent_version,
            expected_host_family=expected_host_family,
        )

    async def probe(self) -> RuntimeProbe:
        try:
            adapter = self._current_adapter()
        except (OSError, ValueError):
            self._clear_adapter()
            return RuntimeProbe(
                runtime_id=self.runtime_id,
                available=False,
                reason="managed_host_unavailable",
            )
        probe = await adapter.probe()
        if not probe.available and probe.reason in {
            "managed_host_unavailable",
            "session_rejected",
        }:
            self._clear_adapter()
        return probe

    async def health(self) -> CadPortResult:
        return await self._call_with_reload("health")

    async def drawing_info(self) -> CadPortResult:
        return await self._call_with_reload("drawing_info")

    async def entity_snapshot(
        self,
        *,
        limit: int = 512,
        expected_revision: int | None = None,
    ) -> CadPortResult:
        return await self._call_with_reload(
            "entity_snapshot",
            limit=limit,
            expected_revision=expected_revision,
        )

    async def program_command(
        self,
        kind: str,
        *,
        arguments: dict[str, Any],
        deadline_at: str | None,
    ) -> CadPortResult:
        """A started program command is sent once and is never retried here."""

        try:
            adapter = self._current_adapter()
        except (OSError, ValueError):
            self._clear_adapter()
            return CadPortResult(False, error_code="managed_host_unavailable")
        result = await adapter.program_command(
            kind,
            arguments=arguments,
            deadline_at=deadline_at,
        )
        if result.error_code in {
            "managed_host_unavailable",
            "session_rejected",
        }:
            self._clear_adapter()
        return result

    def manifest(self, probe: RuntimeProbe) -> CapabilityManifest:
        if self._adapter is None:
            raise RuntimeError("managed_host_unavailable")
        return self._adapter.manifest(probe)

    def phase8_capability_states(self) -> dict[str, str]:
        if self._adapter is None:
            return {}
        return self._adapter.phase8_capability_states()

    async def _call_with_reload(
        self,
        operation: str,
        **kwargs: Any,
    ) -> CadPortResult:
        for attempt in range(2):
            try:
                adapter = self._current_adapter(force=attempt > 0)
            except (OSError, ValueError):
                self._clear_adapter()
                return CadPortResult(False, error_code="managed_host_unavailable")
            result = await getattr(adapter, operation)(**kwargs)
            if result.error_code not in {
                "managed_host_unavailable",
                "session_rejected",
            }:
                return result
            self._clear_adapter()
        return result

    def _current_adapter(
        self,
        *,
        force: bool = False,
    ) -> ManagedDotNetCadReadPort:
        bootstrap = self._bootstrap_path.read_bytes()
        bootstrap_hash = hashlib.sha256(bootstrap).hexdigest()
        if (
            force
            or self._adapter is None
            or bootstrap_hash != self._bootstrap_hash
        ):
            self._clear_adapter()
            self._adapter = self._adapter_factory(self._bootstrap_path)
            self._bootstrap_hash = bootstrap_hash
        return self._adapter

    def _load_adapter(self, path: Path) -> ManagedDotNetCadReadPort:
        return ManagedDotNetCadReadPort.from_bootstrap(
            path,
            agent_version=self._agent_version,
            expected_host_family=self._expected_host_family,
        )

    def _clear_adapter(self) -> None:
        adapter = self._adapter
        self._adapter = None
        self._bootstrap_hash = None
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
