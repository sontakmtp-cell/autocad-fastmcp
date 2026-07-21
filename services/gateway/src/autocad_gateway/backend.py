"""Composition root for the existing AutoCAD backends."""

from __future__ import annotations

from typing import Any

from autocad_mcp.config import detect_backend


def build_backend() -> Any:
    backend_name = detect_backend()
    if backend_name == "file_ipc":
        from autocad_mcp.backends.safe_file_ipc import SafeFileIPCBackend

        return SafeFileIPCBackend(allow_execute_lisp=False)

    from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

    return EzdxfBackend()
