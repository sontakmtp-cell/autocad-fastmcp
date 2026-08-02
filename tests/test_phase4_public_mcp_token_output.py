from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "phase4_public_mcp_e2e.py"
SPEC = importlib.util.spec_from_file_location("phase4_public_mcp_e2e", SCRIPT)
assert SPEC and SPEC.loader
E2E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E2E)


def test_write_access_token_is_exact_and_replaces_old_file(tmp_path: Path) -> None:
    target = tmp_path / "token.json"
    target.write_text('{"access_token":"old","refresh_token":"must-go"}')

    E2E.write_access_token(target, "fresh-token")

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "access_token": "fresh-token"
    }


def test_write_access_token_restricts_windows_acl(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    import win32api
    import win32con
    import win32security

    target = tmp_path / "token.json"
    E2E.write_access_token(target, "fresh-token")

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        expected_sid = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )[0]
    finally:
        token.Close()
    descriptor = win32security.GetFileSecurity(
        str(target), win32security.DACL_SECURITY_INFORMATION
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert dacl is not None
    assert [dacl.GetAce(index)[2] for index in range(dacl.GetAceCount())] == [
        expected_sid
    ]
