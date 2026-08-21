"""Resolve trusted runtime pins for the canonical latest Gateway launcher.

This helper is read-only. It talks to the current-user Managed .NET Host pipe,
reads its Phase 8 host evidence, probes the active Agent runtime manifest, and
hashes the current compiler module. It performs no CAD mutation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from autocad_contracts import canonical_capability_manifest_hash
from autocad_desktop_agent.runtime.managed_dotnet import ManagedDotNetCadReadPort

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPILER_MODULE = (
    REPO_ROOT
    / "packages"
    / "contracts"
    / "src"
    / "autocad_contracts"
    / "phase8_contracts.py"
)


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


async def _resolve() -> dict[str, str]:
    port = ManagedDotNetCadReadPort.from_default_bootstrap(
        agent_version="latest-runtime-pin-resolver",
        expected_host_family="R25",
    )
    handshake = await port._ensure_handshake()
    host = handshake.get("phase8_host_evidence")
    if not isinstance(host, dict):
        raise RuntimeError("phase8_host_evidence_missing")

    probe = await port.probe()
    manifest = port.manifest(probe)
    capability_manifest_hash = (
        f"sha256:{canonical_capability_manifest_hash(manifest)}"
    )

    required = {
        "runtime_id": host.get("runtime_id"),
        "host_family": host.get("host_family"),
        "host_version": host.get("host_version"),
        "package_id": host.get("package_id"),
        "package_version": host.get("package_version"),
        "package_hash": host.get("package_hash"),
        "operation_registry_version": host.get("operation_registry_version"),
        "operation_registry_hash": host.get("operation_registry_hash"),
    }
    missing = [key for key, value in required.items() if not isinstance(value, str) or not value]
    if missing:
        raise RuntimeError("phase8_host_evidence_incomplete:" + ",".join(missing))

    return {
        **required,
        "capability_manifest_hash": capability_manifest_hash,
        "compiler_package_hash": _file_digest(COMPILER_MODULE),
    }


def main() -> int:
    result = asyncio.run(_resolve())
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
