"""Allowlist-only diagnostics exporter."""

from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_FIELDS = frozenset(
    {
        "agent_version",
        "autocad_version",
        "product",
        "edition",
        "release_year",
        "series",
        "vertical",
        "runtime_id",
        "runtime_role",
        "degradation_reason",
        "host_family",
        "host_version",
        "host_package_version",
        "host_package_hash",
        "host_handshake_state",
        "capability_hash",
        "capability_manifest_hash",
        "registry_version",
        "package_manifest_hash",
        "heartbeat_id",
        "job_id",
        "command_id",
        "correlation_id",
        "connection_stage",
        "safe_error_code",
        "safe_error_type",
    }
)

SHORT_ID_FIELDS = frozenset(
    {"heartbeat_id", "job_id", "command_id", "correlation_id"}
)


def export_diagnostics(
    target: str | Path, *, device_id: str, values: dict[str, Any]
) -> Path:
    payload: dict[str, Any] = {}
    for key in sorted(ALLOWED_FIELDS):
        value = values.get(key)
        if value is None or isinstance(value, (dict, list, tuple, set, bytes)):
            continue
        if isinstance(value, str):
            value = value[:12] if key in SHORT_ID_FIELDS else value[:256]
        payload[key] = value
    payload.update(
        {
            "schema": "cad.agent.diagnostics/1",
            "windows_version": platform.platform(),
            "device_id_short": device_id[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "redaction_report": {
                "allowlist_only": True,
                "excluded": [
                    "token",
                    "private_key",
                    "dpapi_blob",
                    "full_path",
                    "drawing_content",
                    "screenshot",
                    "raw_lisp",
                    "cad_program",
                    "arbitrary_assembly",
                    "pipe_secret",
                    "stack_trace",
                    "memory_dump",
                ],
            },
        }
    )
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
