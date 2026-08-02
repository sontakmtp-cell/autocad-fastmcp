from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "phase10-live-public-evidence.py"
SPEC = importlib.util.spec_from_file_location("phase10_runtime_capture", SCRIPT)
assert SPEC and SPEC.loader
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)
COMMIT = "a" * 40
DEVICE = "device-a"


def _inputs(tmp_path: Path) -> argparse.Namespace:
    gateway = tmp_path / "gateway.json"
    gateway.write_text("{}", encoding="utf-8")
    database = tmp_path / "db.json"
    database.write_text(
        json.dumps(
            {
                "schema_version": "cad.phase10-live-session-evidence/1",
                "implementation_commit": COMMIT,
                "status": "PASS",
                "gate_results": {
                    "integrity_ok": True,
                    "foreign_keys_ok": True,
                    "migrations_ok": True,
                    "active_cad_agent_session_ok": True,
                },
                "scope": {"owner_subject": "owner-a", "device_id": DEVICE},
                "database_checks": {
                    "integrity_check": ["ok"],
                    "foreign_key_check": [],
                    "schema_migrations": [
                        {**item, "applied_at": "2026-08-02T07:00:00+00:00"}
                        for item in CAPTURE._expected_db_migrations()
                    ],
                },
                "active_agent_session_id": "session-a",
                "agent_sessions": [
                    {
                        "session_id": "session-a",
                        "device_id": DEVICE,
                        "owner_subject": "owner-a",
                        "device_status": "online",
                        "connected_at": "2026-08-02T07:00:00+00:00",
                        "disconnected_at": None,
                        "protocol_version": "cad.agent/2",
                        "agent_version": "0.1.0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    host = tmp_path / "bundle" / "Contents" / "R25" / "AutocadMcp.Host.R25.dll"
    host.parent.mkdir(parents=True)
    host.write_bytes(b"host")
    shared = host.parent.parent / "Shared"
    shared.mkdir()
    package_hash = "sha256:" + "b" * 64
    shared.joinpath("package-manifest.json").write_text(
        json.dumps(
            {
                "package_id": "autocad.managed_host.r25",
                "package_version": "0.8.0",
                "package_hash": package_hash,
                "artifacts": {host.name: "c" * 64},
            }
        ),
        encoding="utf-8",
    )
    bootstrap = tmp_path / "managed-host-r25.json"
    bootstrap.write_text(
        json.dumps(
            {
                "protocol_version": "cad.host/1",
                "pipe_name": "phase10-pipe",
                "session_secret_base64": base64.b64encode(b"x" * 32).decode(),
                "host_pid": 20,
                "host_family": "R25",
                "host_version": "0.8.0",
                "package_hash": package_hash,
                "created_at": "2026-08-02T07:05:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return argparse.Namespace(
        gateway_identity=gateway,
        db_evidence=database,
        device_id=DEVICE,
        desktop_agent_pid=10,
        autocad_pid=20,
        managed_host_executable=host,
        bootstrap=bootstrap,
    )


def _patch_authoritative_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CAPTURE, "_git_head", lambda: COMMIT)
    monkeypatch.setattr(
        CAPTURE,
        "_derive_gateway_identity",
        lambda value: {
            "gateway_pid": 30,
            "gateway_service_record": {
                "properties": {"Id": CAPTURE.SERVICE_UNIT},
                "process": {
                    "executable": "/usr/bin/python3.12",
                    "executable_sha256": "sha256:" + "d" * 64,
                },
                "release": {
                    "commit": COMMIT,
                    "working_directory": f"/opt/releases/{COMMIT[:7]}/gateway",
                },
            },
        },
    )
    monkeypatch.setattr(
        CAPTURE,
        "_windows_process",
        lambda pid: {
            "process_id": pid,
            "executable": (
                r"C:\agent\KythuatvangAutoCADAgent.exe"
                if pid == 10
                else r"C:\Program Files\Autodesk\AutoCAD 2025\acad.exe"
            ),
            "executable_sha256": "sha256:" + "e" * 64,
            "started_at": "2026-08-02T07:00:00+00:00",
        },
    )
    monkeypatch.setattr(CAPTURE, "_windows_file_version", lambda path: "31.0.58.0")
    original_sha = CAPTURE._sha256
    monkeypatch.setattr(
        CAPTURE,
        "_sha256",
        lambda path: "sha256:" + "c" * 64
        if Path(path).name == "AutocadMcp.Host.R25.dll"
        else original_sha(path),
    )


def test_capture_runtime_identity_builds_valid_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _inputs(tmp_path)
    _patch_authoritative_sources(monkeypatch)

    identity = asyncio.run(CAPTURE._capture_runtime_identity(args, None))

    assert identity["gateway_process"]["release_commit"] == COMMIT
    assert identity["desktop_agent_process"]["standalone"] is True
    assert identity["autocad_process"]["process_id"] == 20
    assert identity["agent_session"]["managed_host"]["process_id"] == 20
    assert "session_secret_base64" not in json.dumps(identity)


def test_capture_runtime_identity_rejects_inactive_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _inputs(tmp_path)
    value = json.loads(args.db_evidence.read_text(encoding="utf-8"))
    value["agent_sessions"][0]["disconnected_at"] = "2026-08-02T07:06:00+00:00"
    args.db_evidence.write_text(json.dumps(value), encoding="utf-8")
    _patch_authoritative_sources(monkeypatch)

    with pytest.raises(ValueError, match="does not contain one active"):
        asyncio.run(CAPTURE._capture_runtime_identity(args, None))


def test_capture_runtime_identity_rejects_failed_db_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _inputs(tmp_path)
    value = json.loads(args.db_evidence.read_text(encoding="utf-8"))
    value["status"] = "FAIL"
    args.db_evidence.write_text(json.dumps(value), encoding="utf-8")
    _patch_authoritative_sources(monkeypatch)

    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(CAPTURE._capture_runtime_identity(args, None))


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value["database_checks"].update(
                {"integrity_check": ["corrupt"]}
            ),
            "does not match",
        ),
        (
            lambda value: value["database_checks"]["schema_migrations"][0].update(
                {"checksum": "0" * 64}
            ),
            "does not match",
        ),
    ],
)
def test_capture_runtime_identity_rejects_tampered_session_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    expected: str,
) -> None:
    args = _inputs(tmp_path)
    value = json.loads(args.db_evidence.read_text(encoding="utf-8"))
    mutate(value)
    args.db_evidence.write_text(json.dumps(value), encoding="utf-8")
    _patch_authoritative_sources(monkeypatch)

    with pytest.raises(ValueError, match=expected):
        asyncio.run(CAPTURE._capture_runtime_identity(args, None))


def test_capture_runtime_identity_cli_does_not_require_token(tmp_path: Path) -> None:
    args = CAPTURE.build_parser().parse_args(
        [
            "capture-runtime-identity",
            "--gateway-identity",
            str(tmp_path / "gateway.json"),
            "--db-evidence",
            str(tmp_path / "db.json"),
            "--device-id",
            DEVICE,
            "--desktop-agent-pid",
            "10",
            "--autocad-pid",
            "20",
            "--managed-host-executable",
            str(tmp_path / "host.dll"),
            "--output",
            str(tmp_path / "identity.json"),
        ]
    )
    assert args.action == "capture-runtime-identity"
    assert not hasattr(args, "token_file")
