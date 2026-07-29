from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import struct
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from autocad_contracts import canonical_json

from autocad_desktop_agent.config import AgentConfig, RuntimeMode
from autocad_desktop_agent.executor import DrawingInfoExecutor
from autocad_desktop_agent.runtime.broker import RuntimeBroker
from autocad_desktop_agent.runtime.managed_dotnet import (
    CadPortResult,
    ManagedDotNetCadReadPort,
    NamedPipeJsonTransport,
    ReloadingManagedDotNetCadReadPort,
)
from autocad_desktop_agent.state import (
    AgentViewState,
    RuntimeState,
    runtime_user_label,
)

SECRET = b"s" * 32
PACKAGE = {
    "package_id": "autocad.lisp.drawing_info",
    "version": "3.3-c1",
    "sha256": "a" * 64,
}


def test_managed_host_preserves_safe_preview_mismatch():
    assert ManagedDotNetCadReadPort._safe_error(
        RuntimeError("preview_mismatch")
    ) == "preview_mismatch"
    assert ManagedDotNetCadReadPort._safe_error(
        RuntimeError("approval_binding_mismatch")
    ) == "approval_binding_mismatch"


class HostTransport:
    def __init__(
        self,
        *,
        family: str = "R25",
        crash: bool = False,
        health_status: str = "ready",
        layers_truncated: bool = False,
        public_truncated: bool | None = None,
    ) -> None:
        self.family = family
        self.crash = crash
        self.health_status = health_status
        self.layers_truncated = layers_truncated
        self.public_truncated = public_truncated
        self.calls: list[str] = []
        self.requests: list[dict] = []
        self.closed = False

    def close(self):
        self.closed = True

    async def request(self, request):
        if self.crash:
            raise EOFError("pipe closed")
        self.requests.append(request)
        payload = request["payload"]
        self.calls.append(payload.get("operation_id", request["message_type"]))
        if request["message_type"] == "handshake":
            nonce = payload["session_nonce"]
            response_payload = {
                "selected_protocol": "cad.host/1",
                "host_family": self.family,
                "host_version": "0.1.0",
                "package_id": "autocad.managed_host.r25",
                "package_version": "0.1.0",
                "package_hash": f"sha256:{'a' * 64}",
                "session_proof": hmac.new(
                    SECRET,
                    (
                        f"cad.host/1\n{request['session_id']}\n{nonce}"
                    ).encode(),
                    hashlib.sha256,
                ).hexdigest(),
                "product": "AutoCAD Mechanical",
                "edition": "full",
                "release_year": 2025,
                "series": "R25.0",
                "active_document_id": "doc-1",
                "phase8_host_evidence": {
                    "operation_registry_version": "cad.operation-registry/1",
                    "operation_registry_hash": f"sha256:{'b' * 64}",
                },
                "capabilities": [
                    "host.health",
                    "observe.summary",
                    "entity.snapshot.v2",
                    "cad.program.preview",
                    "cad.program.commit",
                    "cad.program.validate",
                ],
            }
            return self._response(request, "handshake_result", response_payload)
        operation = payload["operation_id"]
        result = (
            {
                "status": self.health_status,
                "document_name": "mat-bich.dwg",
                "active_document": "mat-bich.dwg",
                "active_document_id": "doc-1",
                "active_document_revision": "42",
            }
            if operation == "host.health"
            else {
                "preview_id": "preview-1",
                "preview_digest": f"sha256:{'b' * 64}",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
                "planned_operation_count": 1,
                "planned_entity_count": 0,
                "planned_layer_count": 1,
                "transaction_aborted": True,
                "drawing_unchanged": True,
            }
            if operation == "cad.program.preview"
            else {
                "document_id": "doc-1",
                "document_name": r"C:\private\mat-bich.dwg",
                "entity_count": 12,
                "layer_count": 2,
                "layers": ["0", "DIM"],
                "layers_truncated": self.layers_truncated,
            }
        )
        if operation != "host.health" and self.public_truncated is not None:
            result["truncated"] = self.public_truncated
        response_payload = {
            "status": "succeeded",
            "operation_id": operation,
            "result": result,
            "runtime_evidence": {
                "runtime_id": "managed_dotnet",
                "runtime_role": "primary",
                "host_family": self.family,
                "host_version": "0.1.0",
            },
        }
        return self._response(request, "result", response_payload)

    @staticmethod
    def _response(request, message_type, payload):
        return {
            "protocol_version": "cad.host/1",
            "message_type": message_type,
            "session_id": request["session_id"],
            "command_id": request["command_id"],
            "sequence": request["sequence"],
            "deadline_at": request["deadline_at"],
            "payload_hash": hashlib.sha256(
                canonical_json(payload).encode()
            ).hexdigest(),
            "payload": payload,
        }


class CompatibilityAdapter:
    runtime_id = "autolisp_file_ipc"

    async def probe(self):
        from autocad_desktop_agent.runtime.contracts import RuntimeProbe

        return RuntimeProbe(
            runtime_id=self.runtime_id,
            available=True,
            product="AutoCAD",
            edition="full",
            release_year=2025,
            active_document="fallback.dwg",
        )

    async def health(self):
        return SimpleNamespace(
            ok=True,
            payload={"active_document": "fallback.dwg"},
        )

    async def drawing_info(self):
        raise AssertionError("not used")

    def manifest(self, probe):
        from autocad_contracts import CapabilityManifest

        return CapabilityManifest.model_validate(
            {
                "schema_version": "cad.capability/1",
                "registry_version": "cad.program/0",
                "cad_products": [
                    {
                        "product": "AutoCAD",
                        "edition": "full",
                        "release_year": 2025,
                        "runtime": {
                            "id": self.runtime_id,
                            "role": "compatibility_fallback",
                            "package_id": PACKAGE["package_id"],
                            "package_version": PACKAGE["version"],
                        },
                        "capabilities": ["observe.summary"],
                    }
                ],
            }
        )


def config(**updates):
    values = dict(
        gateway_ws_url="wss://gateway.example/agent/ws",
        device_id="device-1",
        device_name="Lab",
        ledger_path=Path("agent.db"),
        package_path=Path("mcp_dispatch.lsp"),
        package_sha256="a" * 64,
        runtime_mode=RuntimeMode.MANAGED_DOTNET,
        managed_host_enabled=True,
    )
    values.update(updates)
    return AgentConfig(**values)


async def test_managed_adapter_handshake_health_and_summary_are_bounded():
    transport = HostTransport()
    adapter = ManagedDotNetCadReadPort(
        transport,
        session_secret=SECRET,
        agent_version="0.1.0",
        expected_host_family="R25",
    )

    probe = await adapter.probe()
    health = await adapter.health()
    drawing = await adapter.drawing_info()
    manifest = adapter.manifest(probe)

    assert probe.available is True
    assert probe.product == "AutoCAD Mechanical"
    assert health.ok is True
    assert "entity.snapshot.v2" in manifest.cad_products[0].capabilities
    assert manifest.registry_version == "cad.operation-registry/1"
    assert manifest.operation_registry_hash == f"sha256:{'b' * 64}"
    assert drawing.payload["document_name"].endswith("mat-bich.dwg")
    assert drawing.payload["truncated"] is False
    assert "layers_truncated" not in drawing.payload
    assert transport.calls == [
        "handshake",
        "host.health",
        "drawing.observe.summary",
    ]
    assert not hasattr(adapter, "execute")
    assert not hasattr(adapter, "commit")


async def test_managed_adapter_sends_typed_program_once():
    transport = HostTransport()
    adapter = ManagedDotNetCadReadPort(
        transport,
        session_secret=SECRET,
        agent_version="0.1.0",
        expected_host_family="R25",
    )
    result = await adapter.program_command(
        "program_preview",
        arguments={
            "program": {"schema_version": "cad.program/0.2"},
            "execution_binding": {"document_id": "doc-1"},
        },
        deadline_at=(
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat(),
    )

    assert result.ok is True
    assert result.payload["transaction_aborted"] is True
    assert transport.calls == ["handshake", "cad.program.preview"]
    deadline_at = transport.requests[-1]["deadline_at"]
    assert deadline_at.endswith("+00:00")
    assert len(deadline_at.split(".", 1)[1].split("+", 1)[0]) == 7


async def test_managed_adapter_binds_phase8_plan_document_in_host_envelope():
    transport = HostTransport()
    adapter = ManagedDotNetCadReadPort(
        transport,
        session_secret=SECRET,
        agent_version="0.1.0",
        expected_host_family="R25",
    )

    result = await adapter.program_command(
        "program_preview",
        arguments={
            "execution_plan": {
                "schema_version": "cad.execution-plan/1",
                "document_id": "doc-phase8",
            },
            "capability_evidence": [],
        },
        deadline_at=(
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat(),
    )

    assert result.ok is True
    assert transport.requests[-1]["payload"]["document_id"] == "doc-phase8"


async def test_managed_adapter_binds_approved_command_id_in_host_envelope():
    transport = HostTransport()
    adapter = ManagedDotNetCadReadPort(
        transport,
        session_secret=SECRET,
        agent_version="0.1.0",
        expected_host_family="R25",
    )

    result = await adapter.program_command(
        "program_commit",
        arguments={
            "execution_plan": {
                "schema_version": "cad.execution-plan/1",
                "document_id": "doc-phase8",
            },
            "approval_binding": {"command_id": "command-approved"},
            "capability_evidence": [],
        },
        deadline_at=(
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).isoformat(),
    )

    assert result.ok is True
    assert transport.requests[-1]["command_id"] == "command-approved"


async def test_executor_reports_managed_primary_without_requiring_lisp_host_fields():
    adapter = ManagedDotNetCadReadPort(
        HostTransport(),
        session_secret=SECRET,
        agent_version="0.1.0",
        expected_host_family="R25",
    )
    broker = RuntimeBroker(config(), [adapter])
    executor = DrawingInfoExecutor(
        SimpleNamespace(),
        PACKAGE,
        "0.1.0",
        runtime_broker=broker,
    )

    presence = await executor.probe()

    assert presence.runtime_state == "online_idle"
    assert presence.runtime_id == "managed_dotnet"
    assert presence.runtime_role == "primary"
    assert presence.host_family == "R25"
    assert presence.document_name == "mat-bich.dwg"
    assert len(presence.capability_manifest_hash) == 64


async def test_full_fallback_is_visible_as_degraded():
    managed = ManagedDotNetCadReadPort(
        HostTransport(crash=True),
        session_secret=SECRET,
        agent_version="0.1.0",
    )
    broker = RuntimeBroker(
        config(allow_full_compat_fallback=True),
        [managed, CompatibilityAdapter()],
    )
    executor = DrawingInfoExecutor(
        SimpleNamespace(),
        PACKAGE,
        "0.1.0",
        runtime_broker=broker,
    )

    presence = await executor.probe()

    assert presence.runtime_state == "degraded_compatibility"
    assert presence.runtime_id == "autolisp_file_ipc"
    assert presence.runtime_role == "compatibility_fallback"
    assert presence.degradation_reason == "managed_host_unavailable"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            AgentViewState(
                device_name="Lab",
                runtime_id="managed_dotnet",
                runtime_role="primary",
            ),
            "Hiệu năng đầy đủ (.NET)",
        ),
        (
            AgentViewState(
                device_name="Lab",
                runtime_id="autolisp_file_ipc",
                runtime_role="primary",
                edition="lt",
            ),
            "Tương thích AutoCAD LT",
        ),
        (
            AgentViewState(
                device_name="Lab",
                runtime_id="autolisp_file_ipc",
                runtime_role="compatibility_fallback",
                edition="full",
            ),
            "Chế độ tương thích giới hạn",
        ),
        (
            AgentViewState(
                device_name="Lab",
                runtime_state=RuntimeState.PLUGIN_REQUIRED,
            ),
            "Chưa sẵn sàng đầy đủ",
        ),
        (
            AgentViewState(
                device_name="Lab",
                runtime_state=RuntimeState.VERSION_MISMATCH,
            ),
            "Thành phần AutoCAD không tương thích",
        ),
    ],
)
def test_runtime_copy_is_product_and_role_aware(state, expected):
    assert runtime_user_label(state) == expected


async def test_host_family_mismatch_and_crash_fail_closed():
    mismatch = ManagedDotNetCadReadPort(
        HostTransport(family="R24"),
        session_secret=SECRET,
        agent_version="0.1.0",
        expected_host_family="R25",
    )
    crashed = ManagedDotNetCadReadPort(
        HostTransport(crash=True),
        session_secret=SECRET,
        agent_version="0.1.0",
    )

    assert (await mismatch.probe()).reason == "runtime_version_mismatch"
    assert (await crashed.probe()).reason == "managed_host_unavailable"


@pytest.mark.parametrize(
    ("host_status", "expected_error"),
    [
        ("no_document", "no_active_document"),
        ("busy", "autocad_busy"),
        ("modal_dialog", "modal_dialog_active"),
    ],
)
async def test_managed_health_maps_host_state(host_status, expected_error):
    adapter = ManagedDotNetCadReadPort(
        HostTransport(health_status=host_status),
        session_secret=SECRET,
        agent_version="0.1.0",
    )

    result = await adapter.health()

    assert result.ok is False
    assert result.error_code == expected_error
    assert result.details["status"] == host_status
    assert result.details["handshake_state"] == "connected"


@pytest.mark.parametrize(
    ("host_status", "expected_runtime_state"),
    [
        ("no_document", "no_document"),
        ("busy", "online_busy_user"),
        ("modal_dialog", "modal_dialog"),
    ],
)
async def test_executor_reports_managed_host_state(
    host_status,
    expected_runtime_state,
):
    adapter = ManagedDotNetCadReadPort(
        HostTransport(health_status=host_status),
        session_secret=SECRET,
        agent_version="0.1.0",
    )
    executor = DrawingInfoExecutor(
        SimpleNamespace(),
        PACKAGE,
        "0.1.0",
        runtime_broker=RuntimeBroker(config(), [adapter]),
    )

    presence = await executor.probe()

    assert presence.runtime_state == expected_runtime_state


async def test_managed_health_rejects_unknown_host_state():
    adapter = ManagedDotNetCadReadPort(
        HostTransport(health_status="unexpected"),
        session_secret=SECRET,
        agent_version="0.1.0",
    )

    result = await adapter.health()

    assert result.ok is False
    assert result.error_code == "protocol_mismatch"


@pytest.mark.parametrize("layers_truncated", [False, True])
async def test_managed_summary_maps_layers_truncated(layers_truncated):
    adapter = ManagedDotNetCadReadPort(
        HostTransport(layers_truncated=layers_truncated),
        session_secret=SECRET,
        agent_version="0.1.0",
    )

    result = await adapter.drawing_info()

    assert result.ok is True
    assert result.payload["truncated"] is layers_truncated
    assert "layers_truncated" not in result.payload


async def test_managed_summary_rejects_contradictory_truncation_fields():
    adapter = ManagedDotNetCadReadPort(
        HostTransport(layers_truncated=True, public_truncated=False),
        session_secret=SECRET,
        agent_version="0.1.0",
    )

    result = await adapter.drawing_info()

    assert result.ok is False
    assert result.error_code == "protocol_mismatch"


async def test_reloading_adapter_discovers_host_started_after_agent(tmp_path):
    bootstrap = tmp_path / "managed-host-r25.json"
    transports = [HostTransport()]

    def factory(_path):
        return ManagedDotNetCadReadPort(
            transports[-1],
            session_secret=SECRET,
            agent_version="0.1.0",
        )

    adapter = ReloadingManagedDotNetCadReadPort(
        bootstrap,
        agent_version="0.1.0",
        adapter_factory=factory,
    )

    assert (await adapter.probe()).available is False

    bootstrap.write_text('{"generation": 1}', encoding="utf-8")

    assert (await adapter.probe()).available is True
    assert (await adapter.health()).ok is True


async def test_reloading_adapter_forwards_entity_snapshot_limit(tmp_path):
    bootstrap = tmp_path / "managed-host-r25.json"
    bootstrap.write_text('{"generation": 1}', encoding="utf-8")
    calls: list[int] = []

    class SnapshotAdapter:
        async def entity_snapshot(self, *, limit: int):
            calls.append(limit)
            return CadPortResult(True, payload={"entities": []})

    adapter = ReloadingManagedDotNetCadReadPort(
        bootstrap,
        agent_version="0.1.0",
        adapter_factory=lambda _path: SnapshotAdapter(),
    )

    result = await adapter.entity_snapshot(limit=37)

    assert result.ok is True
    assert calls == [37]


async def test_reloading_adapter_uses_rotated_bootstrap_after_host_restart(tmp_path):
    bootstrap = tmp_path / "managed-host-r25.json"
    bootstrap.write_text('{"generation": 1}', encoding="utf-8")
    transports = [HostTransport()]
    loaded: list[HostTransport] = []

    def factory(_path):
        transport = transports[-1]
        loaded.append(transport)
        return ManagedDotNetCadReadPort(
            transport,
            session_secret=SECRET,
            agent_version="0.1.0",
        )

    adapter = ReloadingManagedDotNetCadReadPort(
        bootstrap,
        agent_version="0.1.0",
        adapter_factory=factory,
    )

    assert (await adapter.probe()).available is True
    first = transports[-1]
    first.crash = True
    transports.append(HostTransport())
    bootstrap.write_text('{"generation": 2}', encoding="utf-8")

    assert (await adapter.probe()).available is True
    assert loaded == [first, transports[-1]]
    assert first.closed is True


async def test_named_pipe_timeout_aborts_stall_and_reconnects():
    class StalledStream:
        def __init__(self):
            self.closed = threading.Event()

        def write(self, _value):
            return None

        def read(self, _size):
            self.closed.wait()
            return b""

        def close(self):
            self.closed.set()

    class ResponseStream:
        def __init__(self, response):
            body = json.dumps(response).encode()
            self.buffer = bytearray(struct.pack("<I", len(body)) + body)

        def write(self, _value):
            return None

        def read(self, size):
            value = bytes(self.buffer[:size])
            del self.buffer[:size]
            return value

        def close(self):
            return None

    stalled = StalledStream()
    response = {"status": "ready"}
    streams = iter([stalled, ResponseStream(response)])
    transport = NamedPipeJsonTransport(
        "test-pipe",
        timeout_seconds=0.05,
        stream_factory=lambda: next(streams),
    )
    envelope = {
        "deadline_at": (
            datetime.now(timezone.utc) + timedelta(seconds=1)
        ).isoformat(),
        "payload": {},
    }

    with pytest.raises(TimeoutError):
        await transport.request(envelope)
    assert await asyncio.to_thread(stalled.closed.wait, 1)
    assert await transport.request(envelope) == response
