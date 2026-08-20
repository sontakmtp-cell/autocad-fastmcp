"""Use the cad.host frame budget when hashing Managed Host envelopes.

``cad.agent/1`` deliberately caps each JSON string at 64 KiB.  A bounded PNG
preview is larger after base64 encoding, so the Managed Host pipe cannot reuse
the Agent protocol's bounded ``canonical_json`` helper for response hashing.
The host transport still has its own 1 MiB frame limit and the preview payload
is independently capped/validated before it is forwarded to the Gateway.
"""

from __future__ import annotations

import json
from typing import Any

from .runtime import managed_dotnet

_INSTALLED = False


def _host_canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def install_preview_transport_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    managed_dotnet.canonical_json = _host_canonical_json
    _INSTALLED = True
