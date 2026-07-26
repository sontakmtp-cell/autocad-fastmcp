"""Privacy-bounded, fail-open telemetry for the Phase 5 local pilot."""

from __future__ import annotations

import ipaddress
import json
import os
import queue
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .executor import AgentExecutionError, SAFE_BACKEND_ERRORS


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
READ_ONLY_OPERATIONS = frozenset(
    {
        "agent.presence.health",
        "drawing.observe.summary",
    }
)
OUTCOMES = frozenset({"succeeded", "failed"})
RUNTIME_IDS = frozenset({"managed_dotnet", "autolisp_file_ipc"})
RUNTIME_ROLES = frozenset({"primary", "compatibility_fallback", "headless"})
RELEASE_FAMILY_YEARS = {
    "R22": frozenset({2018}),
    "R23": frozenset({2019, 2020}),
    "R24": frozenset({2021, 2022, 2023, 2024}),
    "R25": frozenset({2025, 2026}),
}
SAFE_ERROR_CODES = SAFE_BACKEND_ERRORS | frozenset(
    {
        "none",
        "backend_error",
        "runtime_unavailable",
    }
)
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class TelemetryDefaults:
    runtime_id: str
    runtime_role: str
    release_family: str
    release_year: int


def validate_policy(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    telemetry = value.get("telemetry", {})
    if tuple(telemetry.get("dimensions", ())) != DIMENSIONS:
        raise ValueError("telemetry dimensions do not match the Phase 5 contract")
    if tuple(telemetry.get("measures", ())) != MEASURES:
        raise ValueError("telemetry measures do not match the Phase 5 contract")
    if not PROHIBITED.issubset(telemetry.get("prohibited", ())):
        raise ValueError("telemetry prohibited fields are incomplete")


def build_event(
    *,
    defaults: TelemetryDefaults,
    operation_id: str,
    outcome: str,
    latency_ms: float,
    safe_error_code: str | None = None,
    runtime_id: str | None = None,
    runtime_role: str | None = None,
    release_year: int | None = None,
) -> dict[str, Any]:
    dimensions: dict[str, Any] = {
        "runtime_id": runtime_id or defaults.runtime_id,
        "runtime_role": runtime_role or defaults.runtime_role,
        "release_family": defaults.release_family,
        "release_year": release_year or defaults.release_year,
        "operation_id": operation_id,
        "outcome": outcome,
        "safe_error_code": safe_error_code or "none",
    }
    if operation_id not in READ_ONLY_OPERATIONS:
        raise ValueError("operation_id is not in the read-only telemetry allowlist")
    if outcome not in OUTCOMES:
        raise ValueError("outcome is invalid")
    if dimensions["runtime_id"] not in RUNTIME_IDS:
        raise ValueError("runtime_id is invalid")
    if dimensions["runtime_role"] not in RUNTIME_ROLES:
        raise ValueError("runtime_role is invalid")
    if dimensions["safe_error_code"] not in SAFE_ERROR_CODES:
        raise ValueError("safe_error_code is invalid")
    if (
        not isinstance(dimensions["release_year"], int)
        or not 2000 <= dimensions["release_year"] <= 2100
    ):
        raise ValueError("release_year is invalid")
    if dimensions["release_year"] not in RELEASE_FAMILY_YEARS.get(
        dimensions["release_family"],
        (),
    ):
        raise ValueError("release family/year is invalid")
    for key, value in dimensions.items():
        if key == "release_year":
            continue
        if not isinstance(value, str) or not SAFE_VALUE.fullmatch(value):
            raise ValueError(f"{key} is invalid")
        lowered = value.lower()
        if any(prohibited in lowered for prohibited in PROHIBITED):
            raise ValueError(f"{key} contains a prohibited marker")
    if (
        not isinstance(latency_ms, (int, float))
        or latency_ms < 0
        or latency_ms > 3_600_000
    ):
        raise ValueError("latency_ms is invalid")
    return {
        "schema": SCHEMA,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions,
        "measures": {"count": 1, "latency_ms": round(float(latency_ms), 3)},
    }


class TelemetryClient:
    """Non-blocking exporter; queue overflow and transport errors never fail CAD work."""

    def __init__(
        self,
        endpoint: str,
        *,
        ingest_token: str,
        defaults: TelemetryDefaults,
        queue_size: int = 256,
        timeout_seconds: float = 0.5,
        status_path: Path | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.path != "/ingest/autocad-mcp"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("pilot telemetry endpoint must use the custom HTTP ingest path")
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            if parsed.hostname != "localhost":
                raise ValueError("pilot telemetry endpoint must use a loopback or private IP") from None
        else:
            if not (address.is_loopback or address.is_private):
                raise ValueError("pilot telemetry endpoint must use a loopback or private IP")
        if not 1 <= queue_size <= 4096:
            raise ValueError("telemetry queue_size must be between 1 and 4096")
        if not isinstance(ingest_token, str) or len(ingest_token) < 32:
            raise ValueError("telemetry ingest token must contain at least 32 characters")
        self.endpoint = endpoint
        self.ingest_token = ingest_token
        self.defaults = defaults
        self.timeout_seconds = timeout_seconds
        self.status_path = status_path
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(queue_size)
        self._lock = threading.Lock()
        self._stats = {"accepted": 0, "dropped": 0, "rejected": 0, "export_errors": 0}
        self._worker = threading.Thread(target=self._run, daemon=True, name="phase5-telemetry")
        self._worker.start()

    @classmethod
    def from_env(cls, *, policy_path: Path | None = None) -> TelemetryClient | None:
        if os.environ.get("AUTOCAD_MCP_TELEMETRY_ENABLED", "0") != "1":
            return None
        if policy_path is not None:
            validate_policy(policy_path)
        defaults = TelemetryDefaults(
            runtime_id=os.environ.get("AUTOCAD_MCP_TELEMETRY_RUNTIME_ID", "managed_dotnet"),
            runtime_role=os.environ.get("AUTOCAD_MCP_TELEMETRY_RUNTIME_ROLE", "primary"),
            release_family=os.environ.get("AUTOCAD_MCP_TELEMETRY_RELEASE_FAMILY", "R25"),
            release_year=int(os.environ.get("AUTOCAD_MCP_TELEMETRY_RELEASE_YEAR", "2025")),
        )
        return cls(
            os.environ.get(
                "AUTOCAD_MCP_TELEMETRY_ENDPOINT",
                "http://127.0.0.1:4319/ingest/autocad-mcp",
            ),
            ingest_token=os.environ.get("AUTOCAD_MCP_TELEMETRY_TOKEN", ""),
            defaults=defaults,
            queue_size=int(os.environ.get("AUTOCAD_MCP_TELEMETRY_QUEUE_SIZE", "256")),
            status_path=Path(
                os.environ.get(
                    "AUTOCAD_MCP_TELEMETRY_STATUS_PATH",
                    str(
                        Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                        / "Kythuatvang"
                        / "AutoCADAgent"
                        / "diagnostics"
                        / "telemetry-status.json"
                    ),
                )
            ),
        )

    def record(
        self,
        operation_id: str,
        outcome: str,
        latency_ms: float,
        safe_error_code: str | None = None,
        **runtime: Any,
    ) -> bool:
        try:
            event = build_event(
                defaults=self.defaults,
                operation_id=operation_id,
                outcome=outcome,
                latency_ms=latency_ms,
                safe_error_code=safe_error_code,
                **runtime,
            )
        except (TypeError, ValueError):
            self._increment("rejected")
            return False
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._increment("dropped")
            return False
        self._increment("accepted")
        return True

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def close(self, timeout_seconds: float = 2.0) -> None:
        try:
            self._queue.put(None, timeout=timeout_seconds)
        except queue.Full:
            self._increment("dropped")
        self._worker.join(timeout_seconds)

    def _increment(self, key: str) -> None:
        with self._lock:
            self._stats[key] += 1

    def _persist_status(self) -> None:
        if self.status_path is None:
            return
        with self._lock:
            snapshot = dict(self._stats)
        if self.status_path is not None:
            try:
                self.status_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.status_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(
                        {
                            "schema": "autocad-mcp.telemetry-exporter-status/1",
                            **snapshot,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                temporary.replace(self.status_path)
            except OSError:
                pass

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    return
                body = json.dumps(event, separators=(",", ":")).encode("utf-8")
                request = urllib.request.Request(
                    self.endpoint,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self.ingest_token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                        if response.status != 202:
                            self._increment("export_errors")
                except Exception:
                    self._increment("export_errors")
            finally:
                self._persist_status()
                self._queue.task_done()


class TelemetryDrawingInfoExecutor:
    """Adds telemetry without exposing commands, drawing data, identity, or credentials."""

    def __init__(self, executor: Any, client: TelemetryClient) -> None:
        self._executor = executor
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._executor, name)

    async def probe(self) -> Any:
        started = time.perf_counter()
        try:
            presence = await self._executor.probe()
        except Exception:
            self._client.record(
                "agent.presence.health",
                "failed",
                (time.perf_counter() - started) * 1000,
                "backend_error",
            )
            raise
        code = getattr(presence, "safe_error_code", None)
        self._client.record(
            "agent.presence.health",
            "failed" if code else "succeeded",
            (time.perf_counter() - started) * 1000,
            code,
            runtime_id=getattr(presence, "runtime_id", None),
            runtime_role=getattr(presence, "runtime_role", None),
            release_year=getattr(presence, "release_year", None),
        )
        return presence

    async def execute(self, command: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._executor.execute(command)
        except AgentExecutionError as error:
            self._client.record(
                "drawing.observe.summary",
                "failed",
                (time.perf_counter() - started) * 1000,
                error.code,
            )
            raise
        except Exception:
            self._client.record(
                "drawing.observe.summary",
                "failed",
                (time.perf_counter() - started) * 1000,
                "backend_error",
            )
            raise
        runtime = result.get("execution_evidence", {}).get("runtime", {})
        self._client.record(
            "drawing.observe.summary",
            "succeeded",
            (time.perf_counter() - started) * 1000,
            runtime_id=runtime.get("id"),
            runtime_role=runtime.get("role"),
        )
        return result
