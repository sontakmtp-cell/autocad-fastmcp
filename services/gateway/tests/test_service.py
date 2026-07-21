from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocad_gateway.contracts import (
    CadListDevicesInput,
    CadObserveInput,
    CadQueryInput,
    Principal,
)
from autocad_gateway.services import GatewayError, LOCAL_SUBJECT


@pytest.mark.asyncio
async def test_observe_revision_is_stable_for_same_drawing(services):
    principal = Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))
    first = await services.observe(CadObserveInput(device_id="local-default"), principal, "c1")
    second = await services.observe(CadObserveInput(device_id="local-default"), principal, "c2")

    assert first.document_revision == second.document_revision
    assert first.snapshot_id != second.snapshot_id


@pytest.mark.asyncio
async def test_detail_snapshot_contains_allowlisted_geometry(services):
    principal = Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))
    observed = await services.observe(
        CadObserveInput(device_id="local-default", observation_level="detail"),
        principal,
        "c1",
    )
    value = await services.read_snapshot_entities(
        observed.snapshot_id, principal, correlation_id="c2"
    )
    assert "start" in value or "center" in value
    assert "save_path" not in value
    assert "handle" not in value


@pytest.mark.asyncio
async def test_query_filter_cursor_and_limit_are_stable(services):
    principal = Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))
    observed = await services.observe(
        CadObserveInput(device_id="local-default", observation_level="detail"),
        principal,
        "c1",
    )
    first = await services.query(
        CadQueryInput(snapshot_id=observed.snapshot_id, layers=["0"], limit=1),
        principal,
        "c2",
    )
    assert first.total == 1
    assert first.next_cursor is None
    assert first.entities[0].entity_type == "LINE"

    filtered = await services.query(
        CadQueryInput(snapshot_id=observed.snapshot_id, types=["CIRCLE"]),
        principal,
        "c3",
    )
    assert filtered.total == 1
    assert filtered.entities[0].entity_type == "CIRCLE"


@pytest.mark.asyncio
async def test_wrong_principal_cannot_read_device_snapshot_or_artifact(services):
    owner = Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))
    other = Principal(subject="other-user", scopes=("autocad.read",))
    observed = await services.observe(
        CadObserveInput(device_id="local-default", include_preview_image=True), owner, "c1"
    )

    devices = await services.list_devices(CadListDevicesInput(), other, "c2")
    assert devices.devices == []
    with pytest.raises(GatewayError) as device_error:
        await services.observe(CadObserveInput(device_id="local-default"), other, "c3")
    assert device_error.value.code == "not_found"
    with pytest.raises(GatewayError) as snapshot_error:
        await services.read_snapshot_summary(observed.snapshot_id, other)
    assert snapshot_error.value.code == "not_found"
    with pytest.raises(GatewayError) as artifact_error:
        await services.read_artifact(observed.artifact_refs[0].artifact_id, other)
    assert artifact_error.value.code == "not_found"


@pytest.mark.asyncio
async def test_invalid_or_oversized_preview_is_rejected_without_public_blob(services):
    services.max_image_bytes = 1
    principal = Principal(subject=LOCAL_SUBJECT, scopes=("autocad.read",))
    with pytest.raises(GatewayError) as error:
        await services.observe(
            CadObserveInput(device_id="local-default", include_preview_image=True),
            principal,
            "c1",
        )
    assert error.value.code == "response_too_large"


def test_strict_inputs_reject_extra_and_bad_bounds():
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="s", unexpected=True)
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="s", limit=101)
    with pytest.raises(ValidationError):
        CadQueryInput(snapshot_id="s", types=[str(i) for i in range(17)])
