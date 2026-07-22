"""Credential providers; production C1 UI uses Windows DPAPI."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol


class CredentialProvider(Protocol):
    def load(self) -> str: ...


class EnvironmentCredentialProvider:
    def __init__(self, variable: str = "AUTOCAD_AGENT_DEVICE_CREDENTIAL") -> None:
        self.variable = variable

    def load(self) -> str:
        value = os.environ.get(self.variable, "").strip()
        if not value:
            raise RuntimeError("device credential is not configured")
        return value


class DpapiCredentialProvider:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, credential: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI credential storage requires Windows")
        import win32crypt

        self.path.parent.mkdir(parents=True, exist_ok=True)
        protected = win32crypt.CryptProtectData(
            credential.encode("utf-8"), "AutoCAD Agent device", None, None, None, 0
        )
        self.path.write_bytes(protected)

    def load(self) -> str:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI credential storage requires Windows")
        import win32crypt

        if not self.path.is_file():
            raise RuntimeError("device credential is not configured")
        value = win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 0)[1]
        return value.decode("utf-8")
