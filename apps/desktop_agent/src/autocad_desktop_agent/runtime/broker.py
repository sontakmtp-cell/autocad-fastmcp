"""Policy-driven runtime selection without exposing backend details upstream."""

from __future__ import annotations

from collections.abc import Iterable

from autocad_contracts import (
    ProgramExecutionBinding,
    RuntimeEvidence,
    canonical_capability_manifest_hash,
    operation_registry_digest,
)

from ..config import AgentConfig, RuntimeMode
from .autolisp_file_ipc import AutoLispFileIPCCadReadPort
from .contracts import BrokerSelection, CadRuntimeAdapter


class RuntimeSelectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeBroker:
    """Select one explicit adapter; only read fallback is available in Phase 5.0."""

    def __init__(
        self,
        config: AgentConfig,
        adapters: Iterable[CadRuntimeAdapter] | None = None,
    ) -> None:
        initial = list(adapters) if adapters is not None else [
            AutoLispFileIPCCadReadPort(package_version=config.package_version)
        ]
        self._adapters = {adapter.runtime_id: adapter for adapter in initial}
        if len(self._adapters) != len(initial):
            raise ValueError("runtime adapter IDs must be unique")
        self._config = config

    async def select_read_runtime(self) -> BrokerSelection:
        requested = self._config.runtime_mode
        if requested == RuntimeMode.AUTO:
            if self._config.managed_host_enabled and "managed_dotnet" in self._adapters:
                selection, reason = await self._try_adapter(
                    "managed_dotnet", requested.value
                )
                if selection is not None:
                    return selection
            else:
                reason = None
            return await self._select_compatibility(
                requested_runtime="managed_dotnet" if self._config.managed_host_enabled else None,
                degraded=self._config.managed_host_enabled,
                reason=(
                    (reason or "managed_host_unavailable")
                    if self._config.managed_host_enabled else None
                ),
            )
        if requested == RuntimeMode.MANAGED_DOTNET:
            selection, reason = await self._try_adapter(
                "managed_dotnet", requested.value
            )
            if selection is not None:
                return selection
            if self._config.allow_full_compat_fallback:
                return await self._select_compatibility(
                    requested_runtime=requested.value,
                    degraded=True,
                    reason=reason or "managed_host_unavailable",
                )
            raise RuntimeSelectionError(reason or "managed_host_unavailable")
        if requested == RuntimeMode.AUTOLISP_COMPAT:
            return await self._select_compatibility()
        selection, reason = await self._try_adapter(
            "ezdxf_headless", requested.value
        )
        if selection is None:
            raise RuntimeSelectionError(reason or "runtime_unavailable")
        return selection

    async def describe_managed_runtime(self) -> BrokerSelection:
        selection, reason = await self._try_adapter(
            "managed_dotnet",
            "managed_dotnet",
        )
        if selection is None:
            raise RuntimeSelectionError(reason or "managed_host_unavailable")
        return selection

    async def select_write_runtime(
        self,
        binding: ProgramExecutionBinding,
        *,
        required_capability: str,
        required_capabilities: Iterable[str] = (),
        write_lock_enabled: bool,
        write_required: bool = True,
    ) -> BrokerSelection:
        """Select the exact R25 Managed Host. Write never has a fallback."""

        if not self._config.program_v0_enabled:
            raise RuntimeSelectionError("feature_disabled")
        if write_required and not self._config.managed_write_enabled:
            raise RuntimeSelectionError("feature_disabled")
        if self._config.lt_write_enabled:
            raise RuntimeSelectionError("capability_missing")
        if write_required and (
            not self._config.phase6_allowed_device_ids
            or self._config.device_id not in self._config.phase6_allowed_device_ids
        ):
            raise RuntimeSelectionError("device_not_allowed")
        if write_required and not write_lock_enabled:
            raise RuntimeSelectionError("write_lock_disabled")
        if binding.runtime_id != "managed_dotnet" or binding.runtime_role != "primary":
            raise RuntimeSelectionError("runtime_mismatch")
        if binding.policy_version != self._config.program_policy_version:
            raise RuntimeSelectionError("policy_mismatch")
        selection = await self.describe_managed_runtime()
        product = selection.manifest.cad_products[0] if selection.manifest.cad_products else None
        runtime = selection.evidence
        if (
            product is None
            or selection.probe.edition != "full"
            or selection.probe.release_year != 2025
            or runtime.id != binding.runtime_id
            or runtime.role != binding.runtime_role
            or runtime.host_family != binding.host_family
            or runtime.host_family != "R25"
            or runtime.host_version != binding.host_version
            or runtime.package_id != binding.package_id
            or runtime.package_version != binding.package_version
            or runtime.package_hash != binding.package_hash
        ):
            raise RuntimeSelectionError("runtime_mismatch")
        manifest_hash = canonical_capability_manifest_hash(selection.manifest)
        if f"sha256:{manifest_hash}" != binding.capability_manifest_hash:
            raise RuntimeSelectionError("capability_mismatch")
        if selection.manifest.registry_version != binding.operation_registry_version:
            raise RuntimeSelectionError("registry_mismatch")
        manifest_registry_hash = getattr(
            selection.manifest,
            "operation_registry_hash",
            None,
        )
        if (
            manifest_registry_hash != binding.operation_registry_hash
            or manifest_registry_hash != operation_registry_digest()
        ):
            raise RuntimeSelectionError("registry_mismatch")
        required = {required_capability, *required_capabilities}
        if not required.issubset(product.capabilities):
            raise RuntimeSelectionError("capability_missing")
        if not hasattr(selection.adapter, "program_command"):
            raise RuntimeSelectionError("capability_missing")
        return selection

    async def _select_compatibility(
        self,
        *,
        requested_runtime: str | None = None,
        degraded: bool = False,
        reason: str | None = None,
    ) -> BrokerSelection:
        if not self._config.lt_runtime_enabled:
            raise RuntimeSelectionError("lt_runtime_disabled")
        selection, compatibility_reason = await self._try_adapter(
            "autolisp_file_ipc", requested_runtime
        )
        if selection is None:
            raise RuntimeSelectionError(compatibility_reason or "runtime_unavailable")
        if degraded:
            return BrokerSelection(
                adapter=selection.adapter,
                probe=selection.probe,
                evidence=selection.evidence,
                manifest=selection.manifest,
                degraded=True,
                degradation_reason=reason,
                requested_runtime=requested_runtime,
            )
        return selection

    async def _try_adapter(
        self,
        runtime_id: str,
        requested_runtime: str | None,
    ) -> tuple[BrokerSelection | None, str | None]:
        adapter = self._adapters.get(runtime_id)
        if adapter is None:
            return None, None
        probe = await adapter.probe()
        if not probe.available:
            return None, probe.reason
        manifest = adapter.manifest(probe)
        product = manifest.cad_products[0] if manifest.cad_products else None
        evidence = (
            product.runtime
            if product is not None
            else RuntimeEvidence(id=runtime_id, role="headless")
        )
        capability_states = {}
        state_provider = getattr(adapter, "phase8_capability_states", None)
        if callable(state_provider):
            provided = state_provider()
            if isinstance(provided, dict):
                capability_states = dict(provided)
        return (
            BrokerSelection(
                adapter=adapter,
                probe=probe,
                evidence=evidence,
                manifest=manifest,
                capability_states=capability_states,
                requested_runtime=requested_runtime,
            ),
            None,
        )
