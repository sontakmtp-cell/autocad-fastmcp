from __future__ import annotations

import importlib.util
import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR_PATH = ROOT / "deploy" / "telemetry" / "collector.py"
SPEC = importlib.util.spec_from_file_location("phase5_telemetry_collector", COLLECTOR_PATH)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
TOKEN = "phase5-local-test-token-at-least-32"
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


def event(**dimensions):
    values = {
        "runtime_id": "managed_dotnet",
        "runtime_role": "primary",
        "release_family": "R25",
        "release_year": 2025,
        "operation_id": "drawing.observe.summary",
        "outcome": "succeeded",
        "safe_error_code": "none",
    }
    values.update(dimensions)
    return {
        "schema": "autocad-mcp.telemetry/1",
        "recorded_at": "2026-07-25T00:00:00+00:00",
        "dimensions": values,
        "measures": {"count": 1, "latency_ms": 10.5},
    }


def test_collector_persists_aggregates_only(tmp_path: Path) -> None:
    output = tmp_path / "aggregate.json"
    store = collector.AggregateStore(output, max_series=4)
    assert store.accept(event())
    assert store.accept(event())
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored["series"][0]["count"] == 2
    assert stored["series"][0]["latency_avg_ms"] == 10.5
    serialized = output.read_text(encoding="utf-8").lower()
    for prohibited in collector.PROHIBITED:
        assert prohibited not in serialized
    assert "recorded_at" not in stored["series"][0]
    restored = collector.AggregateStore(output, max_series=4)
    assert restored.snapshot()["series"][0]["count"] == 2
    assert restored.accept(event())
    assert restored.snapshot()["series"][0]["count"] == 3


def test_collector_rejects_unknown_or_sensitive_fields(tmp_path: Path) -> None:
    store = collector.AggregateStore(tmp_path / "aggregate.json", max_series=4)
    with_identity = event()
    with_identity["dimensions"]["owner_subject"] = "user-a"
    assert not store.accept(with_identity)
    with_payload = event()
    with_payload["drawing_content"] = "secret"
    assert not store.accept(with_payload)
    assert store.snapshot()["rejected"] == 2


def test_collector_bounds_series_cardinality(tmp_path: Path) -> None:
    store = collector.AggregateStore(tmp_path / "aggregate.json", max_series=1)
    assert store.accept(event())
    assert not store.accept(event(safe_error_code="autocad_busy", outcome="failed"))
    snapshot = store.snapshot()
    assert snapshot["series_count"] == 1
    assert snapshot["dropped_series"] == 1


def test_collector_rejects_write_operation(tmp_path: Path) -> None:
    store = collector.AggregateStore(tmp_path / "aggregate.json", max_series=4)
    assert not store.accept(event(operation_id="cad.program.commit"))


def test_custom_http_ingest_and_dashboard(tmp_path: Path) -> None:
    store = collector.AggregateStore(tmp_path / "aggregate.json", max_series=4)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        collector.handler_for(store, TOKEN_HASH),
    )
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request = urllib.request.Request(
            f"{base}/ingest/autocad-mcp",
            data=json.dumps(event()).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 202
        with urllib.request.urlopen(f"{base}/dashboard", timeout=2) as response:
            assert response.status == 200
            assert b"Telemetry Pilot" in response.read()
        unauthorized = urllib.request.Request(
            f"{base}/ingest/autocad-mcp",
            data=json.dumps(event()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(unauthorized, timeout=2)
            raise AssertionError("collector accepted telemetry without a token")
        except urllib.error.HTTPError as error:
            assert error.code == 401
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
