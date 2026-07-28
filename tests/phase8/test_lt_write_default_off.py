from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "services" / "gateway" / "src",
    ROOT / "apps" / "desktop_agent" / "src",
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "cad_core" / "src",
    ROOT / "src",
):
    sys.path.insert(0, str(source))

from autocad_desktop_agent.config import AgentConfig  # noqa: E402
from autocad_gateway.app import GatewayConfig  # noqa: E402


def _agent_config(**updates) -> AgentConfig:
    values = {
        "gateway_ws_url": "wss://gateway.example/agent/ws",
        "device_id": "phase8-fixture",
        "device_name": "Phase 8 fixture",
        "ledger_path": Path("agent.db"),
        "package_path": Path("mcp_dispatch.lsp"),
        "package_sha256": "a" * 64,
    }
    values.update(updates)
    return AgentConfig(**values)


def test_lt_write_is_default_off_at_gateway_and_agent():
    assert GatewayConfig().lt_write_enabled is False
    assert _agent_config().lt_write_enabled is False


def test_gateway_rejects_lt_write_even_if_requested():
    with pytest.raises(ValueError, match="forbids LT write"):
        GatewayConfig(lt_write_enabled=True).validate()


def test_agent_rejects_lt_write_even_if_requested():
    with pytest.raises(ValueError, match="LT write is unavailable"):
        _agent_config(lt_write_enabled=True).validate()
