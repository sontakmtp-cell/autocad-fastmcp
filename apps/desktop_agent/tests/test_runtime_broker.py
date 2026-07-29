from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from autocad_contracts import (
    CapabilityManifest,
    ProgramExecutionBinding,
    canonical_capability_manifest_hash,
    operation_registry_digest,
)
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
    program: bool = False

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
                "registry_version": (
                    "cad.program/0.2" if self.program else "cad.program/0"
                ),
                **(
                    {"operation_registry_hash": operation_registry_digest()}
                    if self.program
                    else {}
                ),
                "cad_products": [
                    {
                        "product": probe.product,
                        "edition": probe.edition,
                        "release_year": probe.release_year,
                        "runtime": {
                            "id": self.runtime_id,
                            "role": role,
                            **(
                                {
                                    "host_family": "R25",
                                    "host_version": "0.2.0",
                                    "package_id": "autocad.managed_host.r25",
                                    "package_version": "0.2.0",
                                    "package_hash": f"sha256:{'a' * 64}",
                                }
                                if self.program
                                else {}
                            ),
                        },
                        "capabilities": (
                            [
                                "observe.summary",
                                "cad.program.preview",
                                "cad.program.commit",
                                "cad.program.validate",
                            ]
                            if self.program
                            else ["observe.summary"]
                        ),
                    }
                ],
            }
        )

    async def program_command(self, *args, **kwargs):
        return SimpleNamespace(ok=True, payload={})


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


def test_phase8_operation_pack_versions_accept_contract_separator(monkeypatch):
    monkeypatch.setenv("AUTOCAD_AGENT_GATEWAY_WS_URL", "wss://gateway.example/agent/ws")
    monkeypatch.setenv("AUTOCAD_AGENT_DEVICE_ID", "device-a")
    monkeypatch.setenv("AUTOCAD_AGENT_PACKAGE_SHA256", "a" * 64)
    monkeypatch.setenv(
        "AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST",
        "compiler.core/1,create-equivalent/1,transform.exact/1",
    )

    config = AgentConfig.from_env()

    assert config.operation_pack_allowlist == frozenset(
        {"compiler.core/1", "create-equivalent/1", "transform.exact/1"}
    )


def test_operation_pack_allowlist_rejects_path_like_values(monkeypatch):
    monkeypatch.setenv("AUTOCAD_AGENT_GATEWAY_WS_URL", "wss://gateway.example/agent/ws")
    monkeypatch.setenv("AUTOCAD_AGENT_DEVICE_ID", "device-a")
    monkeypatch.setenv("AUTOCAD_AGENT_PACKAGE_SHA256", "a" * 64)
    monkeypatch.setenv("AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST", "compiler/../escape")

    with pytest.raises(
        ValueError,
        match="AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST contains a malformed operation pack",
    ):
        AgentConfig.from_env()


def test_phase6_write_flags_fail_closed(monkeypatch):
    monkeypatch.setenv("AUTOCAD_AGENT_GATEWAY_WS_URL", "wss://gateway.example/agent/ws")
    monkeypatch.setenv("AUTOCAD_AGENT_DEVICE_ID", "device-a")
    monkeypatch.setenv("AUTOCAD_AGENT_PACKAGE_SHA256", "a" * 64)
    monkeypatch.setenv("AUTOCAD_MCP_LT_WRITE_ENABLED", "1")
    with pytest.raises(ValueError, match="LT write is unavailable"):
        AgentConfig.from_env()


async def test_write_selection_is_exact_r25_and_never_falls_back():
    managed = FakeAdapter("managed_dotnet", program=True)
    compat = FakeAdapter("autolisp_file_ipc")
    config = _config(
        runtime_mode=RuntimeMode.AUTO,
        managed_host_enabled=True,
        program_v0_enabled=True,
        managed_write_enabled=True,
        phase6_allowed_device_ids=frozenset({"device-a"}),
        program_policy_version="phase6-low-risk-v1",
    )
    broker = RuntimeBroker(config, [compat, managed])
    manifest = managed.manifest(await managed.probe())
    binding = ProgramExecutionBinding(
        program_digest=f"sha256:{'1' * 64}",
        execution_digest=f"sha256:{'2' * 64}",
        document_id="doc-1",
        document_revision="42",
        runtime_id="managed_dotnet",
        runtime_role="primary",
        host_family="R25",
        host_version="0.2.0",
        package_id="autocad.managed_host.r25",
        package_version="0.2.0",
        package_hash=f"sha256:{'a' * 64}",
        capability_manifest_hash=(
            f"sha256:{canonical_capability_manifest_hash(manifest)}"
        ),
        operation_registry_version="cad.program/0.2",
        operation_registry_hash=operation_registry_digest(),
        policy_version="phase6-low-risk-v1",
    )

    selection = await broker.select_write_runtime(
        binding,
        required_capability="cad.program.commit",
        write_lock_enabled=True,
    )
    assert selection.adapter is managed

    unavailable = RuntimeBroker(config, [compat])
    with pytest.raises(RuntimeSelectionError, match="managed_host_unavailable"):
        await unavailable.select_write_runtime(
            binding,
            required_capability="cad.program.commit",
            write_lock_enabled=True,
        )


async def test_phase8_selection_uses_the_pinned_host_registry() -> None:
    managed = FakeAdapter("managed_dotnet", program=True)
    config = _config(
        runtime_mode=RuntimeMode.AUTO,
        managed_host_enabled=True,
        program_v0_enabled=True,
        managed_write_enabled=True,
        phase6_allowed_device_ids=frozenset({"device-a"}),
        program_policy_version="phase8-policy/1",
    )
    host_registry_hash = f"sha256:{'b' * 64}"
    original_manifest = managed.manifest(await managed.probe())
    phase8_manifest = original_manifest.model_copy(
        update={
            "registry_version": "cad.operation-registry/1",
            "operation_registry_hash": host_registry_hash,
            "cad_products": [
                original_manifest.cad_products[0].model_copy(
                    update={
                        "capabilities": [
                            *original_manifest.cad_products[0].capabilities,
                            "cad.program.v1.compile",
                        ]
                    }
                )
            ],
        }
    )
    managed.manifest = lambda probe: phase8_manifest
    binding = SimpleNamespace(
        execution_plan_digest=f"sha256:{'1' * 64}",
        runtime_id="managed_dotnet",
        runtime_role="primary",
        host_family="R25",
        host_version="0.2.0",
        package_id="autocad.managed_host.r25",
        package_version="0.2.0",
        package_hash=f"sha256:{'a' * 64}",
        capability_manifest_hash=(
            f"sha256:{canonical_capability_manifest_hash(phase8_manifest)}"
        ),
        operation_registry_version="cad.operation-registry/1",
        operation_registry_hash=host_registry_hash,
        policy_version="phase8-policy/1",
    )

    selection = await RuntimeBroker(config, [managed]).select_write_runtime(
        binding,
        required_capability="cad.program.v1.compile",
        write_lock_enabled=True,
    )

    assert selection.manifest.operation_registry_hash == host_registry_hash


async def test_write_selection_rejects_lock_and_binding_changes():
    managed = FakeAdapter("managed_dotnet", program=True)
    config = _config(
        runtime_mode=RuntimeMode.AUTO,
        managed_host_enabled=True,
        program_v0_enabled=True,
        managed_write_enabled=True,
        phase6_allowed_device_ids=frozenset({"device-a"}),
        program_policy_version="phase6-low-risk-v1",
    )
    broker = RuntimeBroker(config, [managed])
    manifest = managed.manifest(await managed.probe())
    binding = ProgramExecutionBinding(
        program_digest=f"sha256:{'1' * 64}",
        execution_digest=f"sha256:{'2' * 64}",
        document_id="doc-1",
        document_revision="42",
        runtime_id="managed_dotnet",
        runtime_role="primary",
        host_family="R25",
        host_version="0.2.0",
        package_id="autocad.managed_host.r25",
        package_version="0.2.0",
        package_hash=f"sha256:{'a' * 64}",
        capability_manifest_hash=(
            f"sha256:{canonical_capability_manifest_hash(manifest)}"
        ),
        operation_registry_version="cad.program/0.2",
        operation_registry_hash=operation_registry_digest(),
        policy_version="phase6-low-risk-v1",
    )
    with pytest.raises(RuntimeSelectionError, match="write_lock_disabled"):
        await broker.select_write_runtime(
            binding,
            required_capability="cad.program.commit",
            write_lock_enabled=False,
        )
    with pytest.raises(RuntimeSelectionError, match="policy_mismatch"):
        await broker.select_write_runtime(
            binding.model_copy(update={"policy_version": "changed"}),
            required_capability="cad.program.commit",
            write_lock_enabled=True,
        )
