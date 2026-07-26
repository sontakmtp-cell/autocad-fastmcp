"""Policy-driven runtime selection without exposing backend details upstream."""

from __future__ import annotations

from collections.abc import Iterable

from autocad_contracts import RuntimeEvidence

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
        return (
            BrokerSelection(
                adapter=adapter,
                probe=probe,
                evidence=evidence,
                manifest=manifest,
                requested_runtime=requested_runtime,
            ),
            None,
        )
