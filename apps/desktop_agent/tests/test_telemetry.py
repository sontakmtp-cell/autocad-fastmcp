from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocad_desktop_agent.executor import AgentExecutionError
from autocad_desktop_agent.telemetry import (
    DIMENSIONS,
    MEASURES,
    PROHIBITED,
    TelemetryClient,
    TelemetryDefaults,
    TelemetryDrawingInfoExecutor,
    build_event,
    validate_policy,
)


ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "native" / "autocad_managed_host" / "packaging" / "phase5-runtime-policy.json"
DEFAULTS = TelemetryDefaults("managed_dotnet", "primary", "R25", 2025)


def test_policy_contract_matches_agent() -> None:
    validate_policy(POLICY)


def test_event_has_only_allowlisted_fields() -> None:
    event = build_event(
        defaults=DEFAULTS,
        operation_id="drawing.observe.summary",
        outcome="succeeded",
        latency_ms=12.3456,
    )
    assert tuple(event["dimensions"]) == DIMENSIONS
    assert tuple(event["measures"]) == MEASURES
    serialized = json.dumps(event).lower()
    assert all(name not in serialized for name in PROHIBITED)
    assert event["measures"] == {"count": 1, "latency_ms": 12.346}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_id", "cad.program.commit"),
        ("runtime_id", "document_path"),
        ("safe_error_code", "device_token"),
        ("release_family", "UserA"),
    ],
)
def test_event_rejects_write_and_prohibited_markers(field: str, value: str) -> None:
    values = {
        "defaults": DEFAULTS,
        "operation_id": "drawing.observe.summary",
        "outcome": "failed",
        "latency_ms": 1,
        "safe_error_code": "backend_error",
    }
    if field == "runtime_id":
        values["defaults"] = TelemetryDefaults(value, "primary", "R25", 2025)
    elif field == "release_family":
        values["defaults"] = TelemetryDefaults("managed_dotnet", "primary", value, 2025)
    else:
        values[field] = value
    with pytest.raises(ValueError):
        build_event(**values)


def test_queue_overflow_is_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entered = False

    def blocked_urlopen(*args, **kwargs):
        nonlocal entered
        entered = True
        raise OSError("collector unavailable")

    monkeypatch.setattr("urllib.request.urlopen", blocked_urlopen)
    client = TelemetryClient(
        "http://127.0.0.1:4319/ingest/autocad-mcp",
        ingest_token="phase5-local-test-token-at-least-32",
        defaults=DEFAULTS,
        queue_size=1,
        status_path=tmp_path / "telemetry-status.json",
    )
    assert client.record("drawing.observe.summary", "succeeded", 1) in {True, False}
    assert client.record("drawing.observe.summary", "succeeded", 1) in {True, False}
    client.close()
    assert entered or client.stats()["dropped"] > 0
    assert client.stats()["accepted"] + client.stats()["dropped"] >= 2
    status = json.loads((tmp_path / "telemetry-status.json").read_text(encoding="utf-8"))
    assert status["schema"] == "autocad-mcp.telemetry-exporter-status/1"
    assert set(status) == {
        "schema",
        "accepted",
        "dropped",
        "rejected",
        "export_errors",
    }


def test_public_or_otlp_endpoint_is_rejected() -> None:
    for endpoint in (
        "https://telemetry.example/v1/metrics",
        "http://8.8.8.8:4319/ingest/autocad-mcp",
        "http://127.0.0.1:4318/v1/metrics",
    ):
        with pytest.raises(ValueError):
            TelemetryClient(
                endpoint,
                ingest_token="phase5-local-test-token-at-least-32",
                defaults=DEFAULTS,
            )


class StubExecutor:
    def __init__(self, error: AgentExecutionError | None = None) -> None:
        self.error = error

    async def probe(self):
        return SimpleNamespace(
            safe_error_code=None,
            runtime_id="managed_dotnet",
            runtime_role="primary",
            release_year=2025,
        )

    async def execute(self, command):
        if self.error:
            raise self.error
        return {
            "execution_evidence": {
                "runtime": {"runtime_id": "managed_dotnet", "runtime_role": "primary"}
            }
        }


class RecordingClient:
    def __init__(self) -> None:
        self.calls = []

    def record(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True


@pytest.mark.asyncio
async def test_executor_records_success_without_payload_or_identity() -> None:
    client = RecordingClient()
    executor = TelemetryDrawingInfoExecutor(StubExecutor(), client)
    sensitive_command = {"owner_subject": "user-b", "document_path": "C:/secret.dwg"}
    await executor.execute(sensitive_command)
    payload = json.dumps(client.calls)
    assert "user-b" not in payload
    assert "secret.dwg" not in payload
    assert client.calls[0][0][0:2] == ("drawing.observe.summary", "succeeded")


@pytest.mark.asyncio
async def test_executor_records_only_safe_error_code() -> None:
    client = RecordingClient()
    executor = TelemetryDrawingInfoExecutor(
        StubExecutor(AgentExecutionError("autocad_busy")),
        client,
    )
    with pytest.raises(AgentExecutionError):
        await executor.execute(object())
    assert client.calls[0][0][0:2] == ("drawing.observe.summary", "failed")
    assert client.calls[0][0][3] == "autocad_busy"
