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
        "capability_hash",
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


def export_diagnostics(
    target: str | Path, *, device_id: str, values: dict[str, Any]
) -> Path:
    payload = {key: values[key] for key in sorted(ALLOWED_FIELDS) if key in values}
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
                    "stack_trace",
                ],
            },
        }
    )
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
