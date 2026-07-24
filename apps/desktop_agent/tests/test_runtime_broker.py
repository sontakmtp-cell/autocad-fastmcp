from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from autocad_contracts import CapabilityManifest
from autocad_desktop_agent.config import AgentConfig, RuntimeMode
from autocad_desktop_agent.runtime import RuntimeBroker, RuntimeProbe, RuntimeSelectionError


def _config(**overrides) -> AgentConfig:
    values = {
        "gateway_ws_url": "wss://gateway.example/agent/ws",
        "device_id": "device-a",
        "device_name": "Lab",
        "ledger_path": Path("agent.db"),
        "package_path": Path("mcp_dispatch.lsp"),
        "package_sha256": "a" * 64,
    }
    values.update(overrides)
    return AgentConfig(**values)


@dataclass
class FakeAdapter:
    runtime_id: str
    available: bool = True
    edition: str = "full"

    async def probe(self):
        return RuntimeProbe(
            runtime_id=self.runtime_id,
            available=self.available,
            product="AutoCAD Mechanical",
            edition=self.edition,
            release_year=2025,
        )

    async def health(self):
        return SimpleNamespace(ok=True)

    async def drawing_info(self):
        return SimpleNamespace(ok=True, payload={})

    def manifest(self, probe):
        role = "primary" if self.runtime_id == "managed_dotnet" else "compatibility_fallback"
        return CapabilityManifest.model_validate(
            {
                "schema_version": "cad.capability/1",
                "registry_version": "cad.program/0",
                "cad_products": [
                    {
                        "product": probe.product,
                        "edition": probe.edition,
                        "release_year": probe.release_year,
                        "runtime": {"id": self.runtime_id, "role": role},
                        "capabilities": ["observe.summary"],
                    }
                ],
            }
        )


async def test_default_mode_keeps_file_ipc_adapter_first():
    adapter = FakeAdapter("autolisp_file_ipc", edition="lt")
    selected = await RuntimeBroker(_config(), [adapter]).select_read_runtime()
    assert selected.adapter is adapter
    assert selected.evidence.id == "autolisp_file_ipc"
    assert selected.degraded is False


async def test_auto_prefers_healthy_managed_host_when_enabled():
    managed = FakeAdapter("managed_dotnet")
    compat = FakeAdapter("autolisp_file_ipc")
    config = _config(runtime_mode=RuntimeMode.AUTO, managed_host_enabled=True)
    selected = await RuntimeBroker(config, [compat, managed]).select_read_runtime()
    assert selected.adapter is managed
    assert selected.evidence.role == "primary"


async def test_managed_read_fallback_is_explicitly_degraded():
    managed = FakeAdapter("managed_dotnet", available=False)
    compat = FakeAdapter("autolisp_file_ipc")
    config = _config(
        runtime_mode=RuntimeMode.MANAGED_DOTNET,
        managed_host_enabled=True,
        allow_full_compat_fallback=True,
    )
    selected = await RuntimeBroker(config, [managed, compat]).select_read_runtime()
    assert selected.adapter is compat
    assert selected.degraded is True
    assert selected.requested_runtime == "managed_dotnet"
    assert selected.degradation_reason == "managed_host_unavailable"


async def test_managed_mode_does_not_silently_fallback():
    config = _config(
        runtime_mode=RuntimeMode.MANAGED_DOTNET,
        managed_host_enabled=True,
        allow_full_compat_fallback=False,
    )
    with pytest.raises(RuntimeSelectionError, match="managed_host_unavailable"):
        await RuntimeBroker(config, [FakeAdapter("autolisp_file_ipc")]).select_read_runtime()


def test_invalid_feature_flag_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTOCAD_AGENT_GATEWAY_WS_URL", "wss://gateway.example/agent/ws")
    monkeypatch.setenv("AUTOCAD_AGENT_DEVICE_ID", "device-a")
    monkeypatch.setenv("AUTOCAD_AGENT_PACKAGE_SHA256", "a" * 64)
    monkeypatch.setenv("AUTOCAD_MCP_MANAGED_HOST_ENABLED", "yes")
    with pytest.raises(ValueError, match="must be 0 or 1"):
        AgentConfig.from_env()
