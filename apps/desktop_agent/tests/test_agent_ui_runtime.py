from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from types import SimpleNamespace

import pytest
from autocad_contracts import canonical_json

from autocad_desktop_agent.config import AgentConfig, RuntimeMode
from autocad_desktop_agent.executor import DrawingInfoExecutor
from autocad_desktop_agent.runtime.broker import RuntimeBroker
from autocad_desktop_agent.runtime.managed_dotnet import (
    ManagedDotNetCadReadPort,
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


class HostTransport:
    def __init__(self, *, family: str = "R25", crash: bool = False) -> None:
        self.family = family
        self.crash = crash
        self.calls: list[str] = []

    async def request(self, request):
        if self.crash:
            raise EOFError("pipe closed")
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
                "capabilities": ["host.health", "observe.summary"],
            }
            return self._response(request, "handshake_result", response_payload)
        operation = payload["operation_id"]
        result = (
            {
                "document_name": "mat-bich.dwg",
                "active_document": "mat-bich.dwg",
            }
            if operation == "host.health"
            else {
                "document_id": "doc-1",
                "document_name": r"C:\private\mat-bich.dwg",
                "entity_count": 12,
                "layer_count": 2,
                "layers": ["0", "DIM"],
            }
        )
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

    assert probe.available is True
    assert probe.product == "AutoCAD Mechanical"
    assert health.ok is True
    assert drawing.payload["document_name"].endswith("mat-bich.dwg")
    assert transport.calls == [
        "handshake",
        "host.health",
        "drawing.observe.summary",
    ]
    assert not hasattr(adapter, "execute")
    assert not hasattr(adapter, "commit")


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
