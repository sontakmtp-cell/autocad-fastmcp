"""Compatibility adapter preserving the existing SafeFileIPC behavior."""

from __future__ import annotations

from autocad_contracts import CapabilityManifest

from ..executor import SafeFileIPCCadReadPort as _LegacySafeFileIPCCadReadPort
from .contracts import RuntimeProbe


class AutoLispFileIPCCadReadPort(_LegacySafeFileIPCCadReadPort):
    """First RuntimeBroker adapter; raw/generated LISP remains disabled."""

    runtime_id = "autolisp_file_ipc"

    def __init__(self, *, package_version: str = "3.3-c1") -> None:
        super().__init__()
        self.package_version = package_version

    async def probe(self) -> RuntimeProbe:
        result = await self.health()
        details = result.payload if result.ok else getattr(result, "details", None)
        details = details if isinstance(details, dict) else {}
        active_document = details.get("active_document")
        return RuntimeProbe(
            runtime_id=self.runtime_id,
            # The packaged compatibility adapter remains eligible even when
            # AutoCAD is closed, busy, modal, or has no active document.
            available=True,
            product=details.get("product") if isinstance(details.get("product"), str) else "AutoCAD",
            edition="lt" if details.get("edition") == "lt" else "full",
            release_year=(
                details.get("release_year")
                if isinstance(details.get("release_year"), int)
                else None
            ),
            series=details.get("series") if isinstance(details.get("series"), str) else None,
            active_document=active_document if isinstance(active_document, str) else None,
            reason=None if result.ok else result.error_code,
        )

    def manifest(self, probe: RuntimeProbe) -> CapabilityManifest:
        role = "primary" if probe.edition == "lt" else "compatibility_fallback"
        return CapabilityManifest.model_validate(
            {
                "schema_version": "cad.capability/1",
                "registry_version": "cad.program/0",
                "cad_products": [
                    {
                        "product": probe.product or "AutoCAD",
                        "edition": probe.edition or "full",
                        "release_year": probe.release_year,
                        "series": probe.series,
                        "runtime": {
                            "id": self.runtime_id,
                            "role": role,
                            "package_id": "autocad.lisp.drawing_info",
                            "package_version": self.package_version,
                        },
                        "capabilities": ["observe.summary"],
                    }
                ],
                "fallback_runtimes": [],
            }
        )


# Keep the established adapter name available to migration code.
SafeFileIPCCadReadPort = AutoLispFileIPCCadReadPort
