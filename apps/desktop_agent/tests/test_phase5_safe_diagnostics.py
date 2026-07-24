from __future__ import annotations

import json

from autocad_desktop_agent.diagnostics import export_diagnostics


def test_runtime_release_diagnostics_are_allowlisted_and_redacted(tmp_path):
    target = export_diagnostics(
        tmp_path / "diagnostics.json",
        device_id="device-runtime-a",
        values={
            "runtime_id": "managed_dotnet",
            "runtime_role": "primary",
            "release_year": 2025,
            "host_family": "R25",
            "host_version": "0.1.0",
            "safe_error_code": "host_busy",
            "owner_subject": "auth0|secret-owner",
            "access_token": "secret-token",
            "pipe_secret": "secret-pipe",
            "document_path": r"C:\private\drawing.dwg",
            "raw_lisp": "(danger)",
            "cad_program": {"operations": [{"op": "line.create"}]},
        },
    )
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert payload["runtime_id"] == "managed_dotnet"
    assert payload["runtime_role"] == "primary"
    assert payload["release_year"] == 2025
    assert payload["host_family"] == "R25"
    assert payload["safe_error_code"] == "host_busy"
    text = target.read_text(encoding="utf-8")
    for secret in (
        "secret-owner",
        "secret-token",
        "secret-pipe",
        r"C:\private\drawing.dwg",
        "(danger)",
        "line.create",
    ):
        assert secret not in text
