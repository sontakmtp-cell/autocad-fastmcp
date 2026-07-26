"""Strict local Phase 5 telemetry collector with an aggregate-only data store."""

from __future__ import annotations

import argparse
import hashlib
import html
import hmac
import ipaddress
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SCHEMA = "autocad-mcp.telemetry/1"
DIMENSIONS = (
    "runtime_id",
    "runtime_role",
    "release_family",
    "release_year",
    "operation_id",
    "outcome",
    "safe_error_code",
)
MEASURES = ("count", "latency_ms")
OPERATIONS = frozenset({"agent.presence.health", "drawing.observe.summary"})
OUTCOMES = frozenset({"succeeded", "failed"})
RUNTIME_IDS = frozenset({"managed_dotnet", "autolisp_file_ipc"})
RUNTIME_ROLES = frozenset({"primary", "compatibility_fallback", "headless"})
RELEASE_FAMILY_YEARS = {
    "R22": frozenset({2018}),
    "R23": frozenset({2019, 2020}),
    "R24": frozenset({2021, 2022, 2023, 2024}),
    "R25": frozenset({2025, 2026}),
}
SAFE_ERROR_CODES = frozenset(
    {
        "none",
        "backend_error",
        "runtime_unavailable",
        "autocad_not_running",
        "no_active_document",
        "autocad_busy",
        "modal_dialog_active",
        "active_document_changed",
        "dispatcher_timeout",
        "dispatcher_not_loaded",
        "command_routing_failed",
        "ipc_result_invalid",
        "managed_host_unavailable",
        "host_not_loaded",
        "protocol_mismatch",
        "runtime_version_mismatch",
        "session_rejected",
    }
)
PROHIBITED = frozenset(
    {
        "owner_subject",
        "access_token",
        "device_token",
        "pipe_secret",
        "document_path",
        "drawing_content",
        "raw_lisp",
        "cad_program",
        "stack_trace",
    }
)
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def validate_event(value: Any) -> tuple[tuple[Any, ...], float]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "recorded_at",
        "dimensions",
        "measures",
    }:
        raise ValueError("event envelope is invalid")
    if value["schema"] != SCHEMA or not isinstance(value["recorded_at"], str):
        raise ValueError("event schema is invalid")
    dimensions = value["dimensions"]
    measures = value["measures"]
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise ValueError("event dimensions are invalid")
    if not isinstance(measures, dict) or set(measures) != set(MEASURES):
        raise ValueError("event measures are invalid")
    if dimensions["operation_id"] not in OPERATIONS:
        raise ValueError("operation is not read-only")
    if dimensions["outcome"] not in OUTCOMES:
        raise ValueError("outcome is invalid")
    if dimensions["runtime_id"] not in RUNTIME_IDS:
        raise ValueError("runtime_id is invalid")
    if dimensions["runtime_role"] not in RUNTIME_ROLES:
        raise ValueError("runtime_role is invalid")
    if dimensions["safe_error_code"] not in SAFE_ERROR_CODES:
        raise ValueError("safe_error_code is invalid")
    if (
        not isinstance(dimensions["release_year"], int)
        or isinstance(dimensions["release_year"], bool)
        or not 2000 <= dimensions["release_year"] <= 2100
    ):
        raise ValueError("release_year is invalid")
    if dimensions["release_year"] not in RELEASE_FAMILY_YEARS.get(
        dimensions["release_family"],
        (),
    ):
        raise ValueError("release family/year is invalid")
    for key, item in dimensions.items():
        if key == "release_year":
            continue
        if not isinstance(item, str) or not SAFE_VALUE.fullmatch(item):
            raise ValueError("dimension value is invalid")
        lowered = item.lower()
        if any(marker in lowered for marker in PROHIBITED):
            raise ValueError("prohibited marker found")
    if type(measures["count"]) is not int or measures["count"] != 1:
        raise ValueError("count must equal one")
    latency = measures["latency_ms"]
    if (
        not isinstance(latency, (int, float))
        or isinstance(latency, bool)
        or latency < 0
        or latency > 3_600_000
    ):
        raise ValueError("latency is invalid")
    return tuple(dimensions[key] for key in DIMENSIONS), float(latency)


class AggregateStore:
    def __init__(self, path: Path, max_series: int) -> None:
        self.path = path
        self.max_series = max_series
        self._lock = threading.Lock()
        self._series: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.rejected = 0
        self.dropped_series = 0
        self.last_received_at: str | None = None
        if self.path.exists():
            self._load()

    def accept(self, value: Any) -> bool:
        try:
            key, latency = validate_event(value)
        except ValueError:
            with self._lock:
                self.rejected += 1
            return False
        with self._lock:
            current = self._series.get(key)
            if current is None:
                if len(self._series) >= self.max_series:
                    self.dropped_series += 1
                    return False
                current = {"count": 0, "latency_sum_ms": 0.0, "latency_max_ms": 0.0}
                self._series[key] = current
            current["count"] += 1
            current["latency_sum_ms"] += latency
            current["latency_max_ms"] = max(current["latency_max_ms"], latency)
            self.last_received_at = datetime.now(timezone.utc).isoformat()
            self._persist()
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        rows = []
        for key, measures in sorted(self._series.items(), key=lambda pair: tuple(map(str, pair[0]))):
            dimensions = dict(zip(DIMENSIONS, key, strict=True))
            count = measures["count"]
            rows.append(
                {
                    **dimensions,
                    "count": count,
                    "latency_avg_ms": round(measures["latency_sum_ms"] / count, 3),
                    "latency_max_ms": round(measures["latency_max_ms"], 3),
                }
            )
        return {
            "schema": "autocad-mcp.telemetry-aggregate/1",
            "updated_at": self.last_received_at,
            "rejected": self.rejected,
            "dropped_series": self.dropped_series,
            "series_count": len(rows),
            "series": rows,
        }

    def _load(self) -> None:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "updated_at",
                "rejected",
                "dropped_series",
                "series_count",
                "series",
            }
            or value["schema"] != "autocad-mcp.telemetry-aggregate/1"
            or not isinstance(value["series"], list)
            or len(value["series"]) > self.max_series
            or value["series_count"] != len(value["series"])
            or (
                value["updated_at"] is not None
                and (
                    not isinstance(value["updated_at"], str)
                    or len(value["updated_at"]) > 64
                )
            )
        ):
            raise ValueError("existing aggregate file is invalid")
        if any(
            type(value[name]) is not int or value[name] < 0
            for name in ("rejected", "dropped_series")
        ):
            raise ValueError("existing aggregate counters are invalid")
        for row in value["series"]:
            expected = set(DIMENSIONS) | {
                "count",
                "latency_avg_ms",
                "latency_max_ms",
            }
            if not isinstance(row, dict) or set(row) != expected:
                raise ValueError("existing aggregate series is invalid")
            key, average = validate_event(
                {
                    "schema": SCHEMA,
                    "recorded_at": "restored",
                    "dimensions": {name: row[name] for name in DIMENSIONS},
                    "measures": {"count": 1, "latency_ms": row["latency_avg_ms"]},
                }
            )
            count = row["count"]
            maximum = row["latency_max_ms"]
            if (
                type(count) is not int
                or count < 1
                or not isinstance(maximum, (int, float))
                or isinstance(maximum, bool)
                or maximum < average
                or maximum > 3_600_000
            ):
                raise ValueError("existing aggregate measures are invalid")
            if key in self._series:
                raise ValueError("existing aggregate contains duplicate series")
            self._series[key] = {
                "count": count,
                "latency_sum_ms": average * count,
                "latency_max_ms": float(maximum),
            }
        self.rejected = value["rejected"]
        self.dropped_series = value["dropped_series"]
        self.last_received_at = value["updated_at"]

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._snapshot_unlocked(), indent=2, ensure_ascii=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def dashboard(snapshot: dict[str, Any]) -> bytes:
    rows = []
    for item in snapshot["series"]:
        cells = "".join(
            f"<td>{html.escape(str(item[key]))}</td>"
            for key in (*DIMENSIONS, "count", "latency_avg_ms", "latency_max_ms")
        )
        rows.append(f"<tr>{cells}</tr>")
    headings = "".join(
        f"<th>{html.escape(key)}</th>"
        for key in (*DIMENSIONS, "count", "latency_avg_ms", "latency_max_ms")
    )
    body = f"""<!doctype html>
<html lang="vi"><meta charset="utf-8"><meta http-equiv="refresh" content="10">
<title>AutoCAD MCP Telemetry Pilot</title>
<style>body{{font:14px Segoe UI;margin:2rem}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.4rem}}th{{background:#eee}}</style>
<h1>AutoCAD MCP Telemetry Pilot</h1>
<p>Chỉ chứa số liệu tổng hợp. Rejected: {snapshot["rejected"]}; dropped series: {snapshot["dropped_series"]}; updated: {html.escape(str(snapshot["updated_at"]))}</p>
<table><thead><tr>{headings}</tr></thead><tbody>{''.join(rows)}</tbody></table></html>"""
    return body.encode("utf-8")


def handler_for(
    store: AggregateStore,
    ingest_token_sha256: str,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/ingest/autocad-mcp":
                self.send_error(404)
                return
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                self.send_error(401)
                return
            supplied_hash = hashlib.sha256(
                authorization[7:].encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(supplied_hash, ingest_token_sha256):
                self.send_error(401)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if not 1 <= length <= 16_384 or self.headers.get_content_type() != "application/json":
                self.send_error(400)
                return
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400)
                return
            if not store.accept(value):
                self.send_error(422)
                return
            self.send_response(202)
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/health":
                body = json.dumps(
                    {"status": "ok", "schema": "autocad-mcp.telemetry-health/1"}
                ).encode("utf-8")
                content_type = "application/json"
            elif self.path == "/metrics":
                body = json.dumps(store.snapshot(), indent=2).encode("utf-8")
                content_type = "application/json"
            elif self.path in {"/", "/dashboard"}:
                body = dashboard(store.snapshot())
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    allowed = {
        "bind_host",
        "port",
        "data_path",
        "max_series",
        "ingest_token_sha256",
    }
    if set(config) != allowed:
        raise ValueError("collector config fields are invalid")
    host = config["bind_host"]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("bind_host must be an IP address") from None
    if address.version != 4 or not (address.is_loopback or address.is_private):
        raise ValueError("bind_host must be loopback or a private IPv4 address")
    token_hash = config["ingest_token_sha256"]
    if (
        not isinstance(token_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", token_hash)
    ):
        raise ValueError("ingest_token_sha256 is invalid")
    store = AggregateStore(Path(config["data_path"]).resolve(), int(config["max_series"]))
    server = ThreadingHTTPServer(
        (host, int(config["port"])),
        handler_for(store, token_hash),
    )
    print(f"Phase 5 telemetry collector listening on http://{host}:{config['port']}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
