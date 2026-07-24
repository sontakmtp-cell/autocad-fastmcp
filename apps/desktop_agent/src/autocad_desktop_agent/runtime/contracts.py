"""Narrow runtime interfaces used during the read-only Phase 5 migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from autocad_contracts import CapabilityManifest, RuntimeEvidence


@dataclass(frozen=True)
class RuntimeProbe:
    runtime_id: str
    available: bool
    product: str | None = None
    edition: Literal["full", "lt", "headless"] | None = None
    release_year: int | None = None
    series: str | None = None
    active_document: str | None = None
    reason: str | None = None


class CadRuntimeAdapter(Protocol):
    runtime_id: str

    async def probe(self) -> RuntimeProbe: ...

    async def health(self) -> Any: ...

    async def drawing_info(self) -> Any: ...

    def manifest(self, probe: RuntimeProbe) -> CapabilityManifest: ...


@dataclass(frozen=True)
class BrokerSelection:
    adapter: CadRuntimeAdapter
    probe: RuntimeProbe
    evidence: RuntimeEvidence
    manifest: CapabilityManifest
    degraded: bool = False
    degradation_reason: str | None = None
    requested_runtime: str | None = None
