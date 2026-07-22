from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cad_core import CadImageAttachment, CadServiceResponse, CommandResult
from fastmcp import Client
from pydantic import ValidationError

from autocad_gateway.app import GatewayConfig, build_mcp_server
from autocad_gateway.contracts import (
    CadListDevicesInput,
    CadObserveInput,
    CadQueryInput,
    Principal,
)
from autocad_gateway.services import (
    DEFAULT_DEVICE_ID,
    LOCAL_SUBJECT,
    GatewayError,
    GatewayServices,
)
from autocad_gateway.snapshots import (
    BoundedSnapshotStore,
    SnapshotRecord,
    encode_cursor,
)
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


PNG = b"\x89PNG\r\n\x1a\nvalid-test-payload"
OWNER = Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))


async def _service(**limits) -> tuple[GatewayServices, EzdxfBackend]:
    backend = EzdxfBackend()
    service = GatewayServices(backend, **limits)
    await service.initialize()
    return service, backend


@pytest.mark.asyncio
async def test_revision_is_level_independent_and_geometry_sensitive():
    service, backend = await _service()
    line = await backend.create_line(0, 0, 10, 0, "0")
    circle = await backend.create_circle(5, 5, 2, "A")

    summary = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c1")
    detail = await service.observe(
        CadObserveInput(device_id=DEFAULT_DEVICE_ID, observation_level="detail"), OWNER, "c2"
    )
    assert summary.document_revision == detail.document_revision

    await backend.entity_move(line.payload["handle"], 3, 4)
    moved_line = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c3")
    assert moved_line.document_revision != summary.document_revision

    circle_entity = backend._doc.entitydb.get(circle.payload["handle"])
    circle_entity.dxf.center = (8, 9, 0)
    circle_entity.dxf.radius = 4
    changed_circle = await service.observe(
        CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c4"
    )
    assert changed_circle.document_revision != moved_line.document_revision

    line_entity = backend._doc.entitydb.get(line.payload["handle"])
    line_entity.dxf.layer = "CHANGED"
    changed_layer = await service.observe(
        CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c5"
    )
    assert changed_layer.document_revision != changed_circle.document_revision


@pytest.mark.asyncio
async def test_revision_changes_for_add_remove_and_ignores_backend_order(monkeypatch):
    service, backend = await _service()
    first = await backend.create_line(0, 0, 1, 0, "0")
    await backend.create_circle(0, 0, 1, "0")
    before = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c1")

    added = await backend.create_line(2, 0, 3, 0, "0")
    after_add = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c2")
    assert after_add.document_revision != before.document_revision
    await backend.entity_erase(added.payload["handle"])
    after_remove = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c3")
    assert after_remove.document_revision == before.document_revision

    original = backend.entity_list

    async def reversed_list(layer=None):
        result = await original(layer)
        result.payload["entities"].reverse()
        return result

    monkeypatch.setattr(backend, "entity_list", reversed_list)
    reordered = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c4")
    assert reordered.document_revision == before.document_revision
    assert first.payload["handle"]


@pytest.mark.asyncio
async def test_unchanged_observations_have_new_snapshot_ids_and_same_revision():
    service, backend = await _service()
    await backend.create_line(0, 0, 1, 1, "0")
    first = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c1")
    second = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c2")
    assert first.snapshot_id != second.snapshot_id
    assert first.document_revision == second.document_revision


@pytest.mark.asyncio
async def test_revision_includes_annotation_state_when_backend_supplies_it(monkeypatch):
    service, backend = await _service()
    created = await backend.create_line(0, 0, 1, 1, "0")
    text = ["NOTE-A"]

    async def entity_detail(entity_id):
        return CommandResult(
            ok=True,
            payload={
                "handle": entity_id,
                "type": "LINE",
                "layer": "0",
                "start": [0, 0],
                "end": [1, 1],
                "text": text[0],
            },
        )

    monkeypatch.setattr(backend, "entity_get", entity_detail)
    first = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c1")
    text[0] = "NOTE-B"
    second = await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c2")
    assert second.document_revision != first.document_revision
    assert created.payload["handle"]


@pytest.mark.asyncio
async def test_entity_detail_and_snapshot_budgets_fail_before_materialization(monkeypatch):
    entity_limited, backend = await _service(max_entities=1)
    await backend.create_line(0, 0, 1, 1, "0")
    await backend.create_line(1, 1, 2, 2, "0")
    with pytest.raises(GatewayError, match="operation failed") as too_many:
        await entity_limited.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c1")
    assert too_many.value.code == "observation_too_large"
    assert entity_limited.snapshot_count == 0

    detail_limited = GatewayServices(backend, max_entity_detail_calls=1)
    with pytest.raises(GatewayError) as detail_budget:
        await detail_limited.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c2")
    assert detail_budget.value.code == "observation_budget_exceeded"
    assert detail_limited.snapshot_count == 0

    byte_limited = GatewayServices(backend, max_snapshot_bytes=1)
    with pytest.raises(GatewayError) as byte_budget:
        await byte_limited.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c3")
    assert byte_budget.value.code == "observation_too_large"
    assert byte_limited.snapshot_count == 0

    normal = GatewayServices(backend)
    observed = await normal.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c4")
    assert observed.entity_count == 2

    async def slow_entity(*, entity_id):
        del entity_id
        await asyncio.sleep(0.05)
        return CommandResult(ok=False)

    timeout = GatewayServices(backend, observation_timeout_seconds=0.001)
    monkeypatch.setattr(timeout.application_service, "get_entity", slow_entity)
    with pytest.raises(GatewayError) as deadline:
        await timeout.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c5")
    assert deadline.value.code == "observation_budget_exceeded"
    assert timeout.snapshot_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attachments", "code"),
    [
        ((), "preview_unavailable"),
        ((CadImageAttachment(mime_type="image/png", data="!!!"),), "preview_unavailable"),
        ((CadImageAttachment(mime_type="image/png", data=""),), "preview_unavailable"),
        (
            (CadImageAttachment(mime_type="image/jpeg", data=base64.b64encode(PNG).decode()),),
            "preview_unavailable",
        ),
        (
            (
                CadImageAttachment(
                    mime_type="image/png", data=base64.b64encode(b"not-png").decode()
                ),
            ),
            "preview_unavailable",
        ),
    ],
)
async def test_preview_rejects_missing_malformed_empty_wrong_mime_and_bad_signature(
    monkeypatch, attachments, code
):
    service, backend = await _service()
    await backend.create_line(0, 0, 1, 1, "0")

    async def screenshot():
        return CadServiceResponse(CommandResult(ok=True), attachments)

    monkeypatch.setattr(service.application_service, "get_screenshot", screenshot)
    with pytest.raises(GatewayError) as error:
        await service.observe(
            CadObserveInput(device_id=DEFAULT_DEVICE_ID, include_preview_image=True),
            OWNER,
            "c1",
        )
    assert error.value.code == code
    assert service.snapshot_count == 0
    assert service.snapshot_store.artifact_count == 0


@pytest.mark.asyncio
async def test_preview_selects_png_attachment_and_enforces_decoded_size(monkeypatch):
    service, backend = await _service()
    await backend.create_line(0, 0, 1, 1, "0")

    async def screenshot():
        return CadServiceResponse(
            CommandResult(ok=True),
            (
                CadImageAttachment(mime_type="text/plain", data="ignored"),
                CadImageAttachment(mime_type="image/png", data=base64.b64encode(PNG).decode()),
            ),
        )

    monkeypatch.setattr(service.application_service, "get_screenshot", screenshot)
    observed = await service.observe(
        CadObserveInput(device_id=DEFAULT_DEVICE_ID, include_preview_image=True), OWNER, "c1"
    )
    assert await service.read_artifact(observed.artifact_refs[0].artifact_id, OWNER) == PNG

    oversized = GatewayServices(backend, max_image_bytes=len(PNG) - 1)
    monkeypatch.setattr(oversized.application_service, "get_screenshot", screenshot)
    with pytest.raises(GatewayError) as error:
        await oversized.observe(
            CadObserveInput(device_id=DEFAULT_DEVICE_ID, include_preview_image=True), OWNER, "c2"
        )
    assert error.value.code == "response_too_large"
    assert oversized.snapshot_count == 0


def _record(number: int, *, owner: str = "owner", artifact: bytes | None = None):
    return SnapshotRecord(
        snapshot_id=f"snapshot-{number}",
        owner_subject=owner,
        device_id="device",
        document_revision=f"revision-{number}",
        observation_level="summary",
        drawing={"entity_count": 0},
        entity_summary={},
        entities=(),
        artifact_id=f"artifact-{number}" if artifact is not None else None,
        artifact_bytes=artifact,
    )


def test_bounded_store_ttl_oldest_first_bytes_owner_and_immutability():
    now = [0.0]
    store = BoundedSnapshotStore(
        ttl_seconds=10,
        max_count=2,
        max_total_bytes=10_000,
        clock=lambda: now[0],
    )
    first = _record(1, artifact=PNG)
    store.add(first)
    store.add(_record(2))
    assert store.get_artifact("artifact-1", "other") is None
    assert store.get_artifact("artifact-1", "owner") == PNG

    class NoLinearScan(OrderedDict):
        def values(self):
            raise AssertionError("artifact lookup must not scan snapshot values")

    store._snapshots = NoLinearScan(store._snapshots)
    assert store.get_artifact("artifact-1", "owner") == PNG

    first.drawing["entity_count"] = 99
    assert store.get_snapshot("snapshot-1", "owner").drawing["entity_count"] == 0
    store.add(_record(3))
    assert store.get_snapshot("snapshot-1", "owner") is None
    assert store.get_artifact("artifact-1", "owner") is None
    assert store.get_snapshot("snapshot-2", "owner") is not None

    now[0] = 11
    assert store.cleanup() == 2
    assert store.snapshot_count == 0

    probe = BoundedSnapshotStore(
        ttl_seconds=10,
        max_count=10,
        max_total_bytes=400,
        clock=lambda: 0,
    )
    probe.add(_record(1))
    probe.add(_record(2))
    assert probe.snapshot_count == 1
    assert probe.get_snapshot("snapshot-2", "owner") is not None


def test_bounded_store_rejects_duplicate_ids_before_any_eviction():
    store = BoundedSnapshotStore(
        ttl_seconds=10,
        max_count=2,
        max_total_bytes=10_000,
        clock=lambda: 0,
    )
    store.add(_record(1, artifact=PNG))
    store.add(_record(2))

    with pytest.raises(ValueError, match="snapshot ID"):
        store.add(_record(1))
    duplicate_artifact = replace(_record(3, artifact=PNG), artifact_id="artifact-1")
    with pytest.raises(ValueError, match="artifact ID"):
        store.add(duplicate_artifact)

    assert store.snapshot_count == 2
    assert store.get_snapshot("snapshot-1", "owner") is not None
    assert store.get_snapshot("snapshot-2", "owner") is not None
    assert store.get_artifact("artifact-1", "owner") == PNG


@pytest.mark.asyncio
async def test_expired_snapshot_and_artifact_are_public_not_found():
    now = [0.0]
    store = BoundedSnapshotStore(
        ttl_seconds=5,
        max_count=10,
        max_total_bytes=1_000_000,
        clock=lambda: now[0],
    )
    backend = EzdxfBackend()
    service = GatewayServices(backend, snapshot_store=store)
    await service.initialize()
    await backend.create_line(0, 0, 1, 1, "0")
    observed = await service.observe(
        CadObserveInput(device_id=DEFAULT_DEVICE_ID, include_preview_image=True), OWNER, "c1"
    )
    artifact_id = observed.artifact_refs[0].artifact_id
    now[0] = 5
    with pytest.raises(GatewayError) as snapshot_error:
        await service.read_snapshot_summary(observed.snapshot_id, OWNER)
    assert snapshot_error.value.code == "not_found"
    with pytest.raises(GatewayError) as artifact_error:
        await service.read_artifact(artifact_id, OWNER)
    assert artifact_error.value.code == "not_found"


@pytest.mark.asyncio
async def test_malformed_backend_rows_and_values_never_materialize_snapshot(monkeypatch):
    service, backend = await _service()

    cases = [
        ["not-an-object"],
        [{"type": "LINE", "layer": "0"}],
        [{"handle": "1", "layer": "0"}],
    ]
    for rows in cases:
        async def entity_list(layer=None, rows=rows):
            del layer
            return CommandResult(ok=True, payload={"entities": rows})

        monkeypatch.setattr(backend, "entity_list", entity_list)
        with pytest.raises(GatewayError) as error:
            await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c")
        assert error.value.code == "backend_error"
        assert service.snapshot_count == 0

    async def valid_list(layer=None):
        del layer
        return CommandResult(
            ok=True,
            payload={"entities": [{"handle": "1", "type": "LINE", "layer": "0"}]},
        )

    monkeypatch.setattr(backend, "entity_list", valid_list)

    async def one_entity_drawing():
        return CommandResult(ok=True, payload={"entity_count": 1, "layers": ["0"], "blocks": []})

    monkeypatch.setattr(backend, "drawing_info", one_entity_drawing)
    for bad_value in [object(), float("nan"), float("inf")]:
        async def bad_detail(entity_id, bad_value=bad_value):
            del entity_id
            return CommandResult(
                ok=True,
                payload={
                    "handle": "1",
                    "type": "LINE",
                    "layer": "0",
                    "start": bad_value,
                },
            )

        monkeypatch.setattr(backend, "entity_get", bad_detail)
        with pytest.raises(GatewayError) as error:
            await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c")
        assert error.value.code == "backend_error"
        assert repr(bad_value) not in str(error.value)
        assert service.snapshot_count == 0


@pytest.mark.asyncio
async def test_entity_detail_identity_drift_and_duplicate_ids_fail_closed(monkeypatch):
    service, backend = await _service()

    async def drawing_info():
        return CommandResult(ok=True, payload={"entity_count": 1, "layers": ["0"]})

    async def one_entity(layer=None):
        del layer
        return CommandResult(
            ok=True,
            payload={"entities": [{"handle": "A", "type": "LINE", "layer": "0"}]},
        )

    monkeypatch.setattr(backend, "drawing_info", drawing_info)
    monkeypatch.setattr(backend, "entity_list", one_entity)
    for detail_payload in (
        {"handle": "B", "type": "LINE", "layer": "0"},
        {"handle": "A", "type": "CIRCLE", "layer": "0"},
        {"handle": "A", "type": "LINE", "layer": "CHANGED"},
    ):
        async def mismatched_detail(entity_id, payload=detail_payload):
            del entity_id
            return CommandResult(ok=True, payload=payload)

        monkeypatch.setattr(backend, "entity_get", mismatched_detail)
        with pytest.raises(GatewayError) as error:
            await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c")
        assert error.value.code == "backend_error"
        assert service.snapshot_count == 0

    async def duplicate_drawing():
        return CommandResult(ok=True, payload={"entity_count": 2, "layers": ["0"]})

    async def duplicate_entities(layer=None):
        del layer
        row = {"handle": "A", "type": "LINE", "layer": "0"}
        return CommandResult(ok=True, payload={"entities": [row, dict(row)]})

    monkeypatch.setattr(backend, "drawing_info", duplicate_drawing)
    monkeypatch.setattr(backend, "entity_list", duplicate_entities)
    with pytest.raises(GatewayError) as error:
        await service.observe(CadObserveInput(device_id=DEFAULT_DEVICE_ID), OWNER, "c")
    assert error.value.code == "backend_error"
    assert service.snapshot_count == 0


class _StatusApplication:
    def __init__(self, status: CommandResult, health: CommandResult):
        self.status = status
        self.health_result = health

    async def get_status(self):
        return self.status

    async def health(self):
        return self.health_result


@pytest.mark.asyncio
async def test_device_online_state_is_confirmed_and_stale_file_ipc_is_offline():
    backend = SimpleNamespace(name="fake", capabilities=SimpleNamespace())

    async def status_value(status, health):
        service = GatewayServices(
            backend, application_service=_StatusApplication(status, health)
        )
        result = await service.list_devices(CadListDevicesInput(), OWNER, "c")
        return result.devices[0].status

    assert await status_value(
        CommandResult(ok=True, payload={"has_document": True}),
        CommandResult(ok=True, payload={"reachable": True}),
    ) == "online"
    assert await status_value(
        CommandResult(ok=True, payload={"has_document": False}),
        CommandResult(ok=True, payload={"reachable": True}),
    ) == "offline"
    assert await status_value(
        CommandResult(ok=True, payload={}),
        CommandResult(ok=True, payload={"reachable": True}),
    ) == "offline"
    assert await status_value(
        CommandResult(ok=True, payload={"has_document": True}),
        CommandResult(ok=True, payload={"reachable": True, "busy": True}),
    ) == "offline"
    assert await status_value(
        CommandResult(ok=True, payload={"has_document": True}),
        CommandResult(ok=True, payload={"reachable": True, "modal_dialog": True}),
    ) == "offline"

    backend.name = "file_ipc"
    assert await status_value(
        CommandResult(ok=True, payload={"active_document": "stale.dwg"}),
        CommandResult(
            ok=False,
            error_code="dispatcher_timeout",
            details={"active_document": "stale.dwg", "dispatcher_reachable": False},
        ),
    ) == "offline"
    failing = GatewayServices(
        backend,
        application_service=_StatusApplication(
            CommandResult(ok=False), CommandResult(ok=False)
        ),
    )
    with pytest.raises(GatewayError) as error:
        await failing.list_devices(CadListDevicesInput(), OWNER, "c")
    assert error.value.code == "backend_error"


def test_filter_bounds_deduplication_canonicalization_and_config_bounds():
    value = CadQueryInput(
        snapshot_id="snapshot-1",
        types=[" circle ", "LINE", "line"],
        layers=[" B ", "A", "A"],
    )
    assert value.types == ["CIRCLE", "LINE"]
    assert value.layers == ["A", "B"]
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="snapshot-1", types=["X" * 65])
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="snapshot-1", layers=["X" * 256])
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="snapshot-1", types=[" "])
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="snapshot-1", types=[str(i) for i in range(17)])
    with pytest.raises(ValidationError):
        CadQueryInput(
            snapshot_id="snapshot-1",
            types=["T" * 64] * 16,
            layers=[f"{index:02d}" + "L" * 253 for index in range(16)],
        )
    with pytest.raises(ValueError):
        GatewayConfig(max_entities=0).validate()
    with pytest.raises(ValueError):
        GatewayConfig(max_entities=10_001).validate()


@pytest.mark.asyncio
async def test_cursor_rejects_malformed_snapshot_filter_and_offsets():
    service, backend = await _service()
    await backend.create_line(0, 0, 1, 0, "A")
    await backend.create_line(0, 1, 1, 1, "B")
    observed = await service.observe(
        CadObserveInput(device_id=DEFAULT_DEVICE_ID, observation_level="detail"), OWNER, "c1"
    )
    first = await service.query(
        CadQueryInput(snapshot_id=observed.snapshot_id, limit=1), OWNER, "c2"
    )
    assert first.next_cursor
    assert len(first.next_cursor) <= 512
    reordered = CadQueryInput(
        snapshot_id=observed.snapshot_id, layers=["B", "A"], limit=1
    )
    same = CadQueryInput(snapshot_id=observed.snapshot_id, layers=["A", "B"], limit=1)
    assert reordered.layers == same.layers

    invalid = [
        "not-base64!",
        encode_cursor(snapshot_id="other", types=[], layers=[], offset=1),
        encode_cursor(snapshot_id=observed.snapshot_id, types=["LINE"], layers=[], offset=1),
        encode_cursor(snapshot_id=observed.snapshot_id, types=[], layers=[], offset=-1),
        encode_cursor(snapshot_id=observed.snapshot_id, types=[], layers=[], offset=2**31),
        encode_cursor(snapshot_id=observed.snapshot_id, types=[], layers=[], offset=999_999),
    ]
    for cursor in invalid:
        with pytest.raises((ValidationError, GatewayError)):
            request = CadQueryInput(snapshot_id=observed.snapshot_id, cursor=cursor, limit=1)
            await service.query(request, OWNER, "c")


@pytest.mark.asyncio
async def test_public_errors_include_correlation_and_mask_internal_values(monkeypatch, services):
    server = build_mcp_server(services, correlation_id_factory=lambda: "corr-safe")
    async with Client(server) as client:
        gateway_error = await client.call_tool(
            "cad_query", {"snapshot_id": "missing"}, raise_on_error=False
        )
        assert gateway_error.is_error
        assert "not_found" in gateway_error.content[0].text
        assert "correlation_id=corr-safe" in gateway_error.content[0].text

        validation = await client.call_tool(
            "cad_query",
            {"snapshot_id": "valid", "types": [" "]},
            raise_on_error=False,
        )
        assert validation.is_error
        assert "invalid_request" in validation.content[0].text
        assert "correlation_id=corr-safe" in validation.content[0].text

        framework_validation = await client.call_tool(
            "cad_query",
            {"snapshot_id": "valid", "types": "LINE"},
            raise_on_error=False,
        )
        assert framework_validation.is_error
        assert "invalid_request" in framework_validation.content[0].text
        assert "correlation_id=corr-safe" in framework_validation.content[0].text

        async def explode(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("secret /private/drawing.dwg")

        monkeypatch.setattr(services, "query", explode)
        unexpected = await client.call_tool(
            "cad_query", {"snapshot_id": "valid"}, raise_on_error=False
        )
        assert unexpected.is_error
        text = unexpected.content[0].text
        assert "internal_error" in text
        assert "correlation_id=corr-safe" in text
        assert "secret" not in text
        assert "/private/" not in text


def test_shutdown_cleanup_is_explicit():
    service = GatewayServices(SimpleNamespace())
    service.snapshot_store.add(_record(1))
    asyncio.run(service.shutdown())
    assert service.snapshot_count == 0
