"""Preview transport compatibility for Managed Host and Agent hello frames.

``cad.agent/1`` deliberately caps each JSON string at 64 KiB. A bounded PNG
preview is larger after base64 encoding, so the Managed Host pipe cannot reuse
the Agent protocol's bounded ``canonical_json`` helper for response hashing.
The host transport still has its own 1 MiB frame limit and the preview payload
is independently capped/validated before it is forwarded to the Gateway.

The Managed Host also advertises preview support in its capability manifest.
Historically ``AgentCore`` projected only program/rollback capabilities from
that manifest into the device Hello frame, which silently dropped
``cad.observe.preview-image/1``. This module preserves that capability only when
the active Managed Host manifest actually declares it.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from autocad_contracts import (
    HelloMessage,
    canonical_capabilities,
    canonical_capability_hash,
)

from .runtime import managed_dotnet

PREVIEW_CAPABILITY = "cad.observe.preview-image/1"
_INSTALLED = False
_ORIGINAL_AGENT_SEND: Any | None = None


def _host_canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _advertise_preview_capability(
    capabilities: Iterable[str],
    capability_manifest: Any | None,
) -> list[str]:
    """Project preview support from the Managed Host manifest into Agent Hello."""

    projected = list(capabilities)
    products = (
        getattr(capability_manifest, "cad_products", ())
        if capability_manifest is not None
        else ()
    )
    supports_preview = any(
        PREVIEW_CAPABILITY in set(getattr(product, "capabilities", ()) or ())
        for product in products
    )
    if supports_preview:
        projected.append(PREVIEW_CAPABILITY)
    return list(canonical_capabilities(projected))


async def _send_with_preview_capability(websocket: Any, message: Any) -> None:
    adjusted = message
    if isinstance(message, HelloMessage):
        capabilities = _advertise_preview_capability(
            message.capabilities,
            message.capability_manifest,
        )
        if capabilities != list(message.capabilities):
            adjusted = message.model_copy(
                update={
                    "capabilities": capabilities,
                    "capability_hash": canonical_capability_hash(capabilities),
                }
            )
    if _ORIGINAL_AGENT_SEND is None:  # pragma: no cover - install invariant
        raise RuntimeError("preview transport compatibility is not installed")
    await _ORIGINAL_AGENT_SEND(websocket, adjusted)


def install_preview_transport_compat() -> None:
    global _INSTALLED, _ORIGINAL_AGENT_SEND
    if _INSTALLED:
        return

    # Import after package __version__ has been initialized to avoid a package
    # initialization cycle: core imports __version__ from this package.
    from .core import AgentCore

    managed_dotnet.canonical_json = _host_canonical_json
    _ORIGINAL_AGENT_SEND = AgentCore._send
    AgentCore._send = staticmethod(_send_with_preview_capability)
    _INSTALLED = True
