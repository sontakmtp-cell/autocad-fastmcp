"""Phase 5 guardrails for the AutoCAD LT/File IPC compatibility runtime."""

from __future__ import annotations

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.backends.safe_file_ipc import (
    SUPPORTED_IPC_COMMANDS,
    SafeFileIPCBackend,
)


async def test_raw_lisp_stays_fail_closed_without_creating_ipc_artifacts(tmp_path):
    backend = SafeFileIPCBackend(allow_execute_lisp=False)
    backend._ipc_dir = tmp_path

    result = await backend.execute_lisp("(progn (vl-load-com) (startapp \"cmd.exe\"))")

    assert result.ok is False
    assert result.error_code == "execute_lisp_denied"
    assert list(tmp_path.iterdir()) == []


async def test_allowlisted_read_dispatch_survives_and_unknown_command_never_routes(
    monkeypatch,
):
    routed: list[tuple[str, dict, bool]] = []

    async def capture(self, command, params, retry_ping=False):
        routed.append((command, params, retry_ping))
        return CommandResult(ok=True, payload={"document_name": "fixture.dwg"})

    monkeypatch.setattr(FileIPCBackend, "_dispatch", capture)
    backend = SafeFileIPCBackend(allow_execute_lisp=False)

    allowed = await backend.drawing_info()
    denied = await backend._dispatch("load-arbitrary-lisp", {"path": "payload.lsp"})

    assert "drawing-info" in SUPPORTED_IPC_COMMANDS
    assert "load-arbitrary-lisp" not in SUPPORTED_IPC_COMMANDS
    assert allowed.ok is True
    assert denied.ok is False
    assert denied.error_code == "unsupported_operation"
    assert routed == [("drawing-info", {}, False)]


async def test_execute_lisp_allowlist_entry_cannot_bypass_runtime_kill_switch(
    monkeypatch,
):
    async def forbidden_route(*args, **kwargs):
        raise AssertionError("disabled execute-lisp must be rejected before File IPC")

    monkeypatch.setattr(FileIPCBackend, "_dispatch", forbidden_route)
    backend = SafeFileIPCBackend(allow_execute_lisp=False)

    result = await backend._dispatch("execute-lisp", {"code_file": "payload.lsp"})

    assert "execute-lisp" in SUPPORTED_IPC_COMMANDS
    assert result.ok is False
    assert result.error_code == "execute_lisp_denied"
