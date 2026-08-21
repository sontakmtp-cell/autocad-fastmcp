"""Pin preview observations to the Managed .NET runtime that advertises them.

The Agent capability handshake may describe the Managed Host even when the
normal read broker is configured to use or temporarily falls back to the
AutoLISP compatibility runtime.  That creates an inconsistent state where the
device advertises ``cad.observe.preview-image/1`` but ``cad_observe`` selects a
port without ``preview_image`` and fails with ``capability_missing``.

Preview capture has no compatibility implementation, so a preview request must
run on the exact Managed Host that advertised the capability.  This shim keeps
normal summary/detail runtime selection unchanged and pins only commands with
``include_preview_image=true``.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .executor import AgentExecutionError, ReadCommandExecutor

PREVIEW_CAPABILITY = "cad.observe.preview-image/1"

_INSTALLED = False
_ORIGINAL_EXECUTE = ReadCommandExecutor.execute
_ORIGINAL_SELECT_PORT = ReadCommandExecutor._select_port
_PINNED_SELECTION: ContextVar[Any | None] = ContextVar(
    "autocad_preview_pinned_selection",
    default=None,
)


async def _select_port_with_preview_pin(
    self: ReadCommandExecutor,
) -> tuple[Any, Any | None]:
    selection = _PINNED_SELECTION.get()
    if selection is not None:
        return selection.adapter, selection
    return await _ORIGINAL_SELECT_PORT(self)


async def _execute_with_preview_runtime_pin(
    self: ReadCommandExecutor,
    command: Any,
) -> dict[str, Any]:
    if not bool(command.payload.get("include_preview_image")):
        return await _ORIGINAL_EXECUTE(self, command)

    broker = self._runtime_broker
    describe_managed = getattr(broker, "describe_managed_runtime", None)
    if broker is None or not callable(describe_managed):
        raise AgentExecutionError("capability_missing")

    try:
        selection = await describe_managed()
    except Exception as error:
        raise AgentExecutionError(
            getattr(error, "code", "managed_host_unavailable")
        ) from error

    product = (
        selection.manifest.cad_products[0]
        if selection.manifest.cad_products
        else None
    )
    if (
        product is None
        or PREVIEW_CAPABILITY not in set(product.capabilities)
        or not callable(getattr(selection.adapter, "preview_image", None))
    ):
        raise AgentExecutionError("capability_missing")

    token = _PINNED_SELECTION.set(selection)
    try:
        return await _ORIGINAL_EXECUTE(self, command)
    finally:
        _PINNED_SELECTION.reset(token)


def install_preview_runtime_selection_compat() -> None:
    """Install preview-specific Managed Host pinning once."""

    global _INSTALLED
    if _INSTALLED:
        return
    ReadCommandExecutor._select_port = _select_port_with_preview_pin
    ReadCommandExecutor.execute = _execute_with_preview_runtime_pin
    _INSTALLED = True
