from __future__ import annotations

import json

import pytest

import autocad_contracts.agent_protocol as agent_protocol
from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict
from autocad_gateway.observe_detail_compat import (
    DETAIL_OBSERVE_MESSAGE_BYTES,
    DETAIL_OBSERVE_STORAGE_BYTES,
    MAX_DETAIL_ENTITIES,
    _ORIGINAL_REPOSITORY_JSON,
    _detail_observation_envelope,
    _parse_agent_message_with_detail_limit,
    _repository_json_with_detail_limit,
)


def _unsupported_entity(index: int) -> dict[str, object]:
    return {
        "entity_id": f"{index + 1:X}",
        "entity_type": "TEXT",
        "layer": "0",
        "space": "model",
        "bounds": None,
        "geometry": None,
        "geometry_truncated": False,
        "fingerprint": "sha256:" + f"{index + 1:064x}"[-64:],
        "geometry_status": "unsupported",
        "geometry_reason": "entity_type_unsupported",
        "source_runtime": "managed_dotnet",
        "source_capabilities": [],
    }


def _dimension_entity() -> dict[str, object]:
    return {
        "entity_id": "1A7F",
        "entity_type": "DIMENSION",
        "layer": "DIM",
        "space": "model",
        "bounds": None,
        "geometry": {
            "ObjectName": "AcDbRotatedDimension",
            "Handle": "1A7F",
            "Layer": "DIM",
            "DimensionStyle": "ISO-25",
            "Measurement": 1250.0,
            "TextOverride": "",
            "TextHeight": 2.5,
            "TextPosition": [100.0, 200.0, 0.0],
            "TextRotation": 0.0,
            "Color": 256,
            "Visible": True,
            "Annotative": False,
        },
        "geometry_truncated": False,
        "fingerprint": "sha256:" + "a" * 64,
        "geometry_status": "exact",
        "geometry_reason": None,
        "source_runtime": "managed_dotnet",
        "source_capabilities": ["entity.properties.dimension/1"],
    }


def _error_entity() -> dict[str, object]:
    return {
        "entity_id": "UNAVAILABLE-1243",
        "entity_type": "UNKNOWN",
        "layer": "<unavailable>",
        "space": "model",
        "bounds": None,
        "geometry": {
            "detail_error": {
                "property": "entity",
                "error_type": "AutodeskRuntimeException",
                "message": "entity read failed",
            }
        },
        "geometry_truncated": False,
        "fingerprint": "sha256:" + "b" * 64,
        "geometry_status": "unavailable",
        "geometry_reason": "entity_read_failed",
        "source_runtime": "managed_dotnet",
        "source_capabilities": [],
    }


def _detail_snapshot(entities: list[dict[str, object]]) -> dict[str, object]:
    return {
        "snapshot_id": "snapshot-command-test",
        "document_revision": "7",
        "observation_level": "detail",
        "drawing": {
            "document_id": "doc-test",
            "document_name": "test.dwg",
            "database_fingerprint": "database-test",
            "entity_count": len(entities),
            "layers": ["0", "DIM"],
            "layer_count": 2,
            "truncated": False,
        },
        "entity_summary": {
            "entity_count": len(entities),
            "detail_available": True,
            "truncated": False,
        },
        "entities": entities,
        "revision_evidence": {
            "revision_schema": "cad.revision/1",
            "revision_strength": "database_object_fingerprint",
            "commit_safe": True,
        },
    }


def test_detail_transport_ceiling_is_bounded_and_larger_than_legacy() -> None:
    assert agent_protocol.MAX_WEBSOCKET_MESSAGE_BYTES == DETAIL_OBSERVE_MESSAGE_BYTES
    assert DETAIL_OBSERVE_MESSAGE_BYTES == 16 * 1024 * 1024
    assert DETAIL_OBSERVE_STORAGE_BYTES < DETAIL_OBSERVE_MESSAGE_BYTES


def test_large_frame_admission_is_detail_only() -> None:
    non_detail = json.dumps(
        {"message_type": "heartbeat", "padding": "x" * 1_100_000}
    )
    with pytest.raises(ValueError, match="protocol byte limit"):
        _parse_agent_message_with_detail_limit(non_detail)

    assert _detail_observation_envelope(
        {
            "message_type": "result",
            "status": "succeeded",
            "result": {"snapshot": {"observation_level": "detail"}},
        }
    )
    assert not _detail_observation_envelope(
        {
            "message_type": "result",
            "status": "succeeded",
            "result": {"snapshot": {"observation_level": "summary"}},
        }
    )


def test_gateway_accepts_dimension_and_entity_error_rows() -> None:
    assert DurableJobService._valid_c1_detail_entity(_dimension_entity())
    assert DurableJobService._valid_c1_detail_entity(_error_entity())
    assert DurableJobService._valid_c1_detail_entity(_unsupported_entity(0))


def test_gateway_accepts_five_thousand_detail_entities() -> None:
    entities = [_unsupported_entity(index) for index in range(MAX_DETAIL_ENTITIES)]
    snapshot = _detail_snapshot(entities)
    assert (
        DurableJobService._validate_c1_detail_observation(
            snapshot,
            revision=snapshot["revision_evidence"],
            drawing=snapshot["drawing"],
            summary=snapshot["entity_summary"],
            managed_dotnet=True,
        )
        is None
    )

    entities.append(_unsupported_entity(MAX_DETAIL_ENTITIES))
    snapshot = _detail_snapshot(entities)
    assert (
        DurableJobService._validate_c1_detail_observation(
            snapshot,
            revision=snapshot["revision_evidence"],
            drawing=snapshot["drawing"],
            summary=snapshot["entity_summary"],
            managed_dotnet=True,
        )
        == "backend_error"
    )


def test_repository_raises_storage_ceiling_only_for_detail_entities() -> None:
    entities = [_unsupported_entity(index) for index in range(4_000)]
    with pytest.raises(RepositoryConflict, match="payload_too_large"):
        _ORIGINAL_REPOSITORY_JSON(entities)

    encoded = _repository_json_with_detail_limit(entities)
    assert len(encoded.encode("utf-8")) > 512_000
    assert len(encoded.encode("utf-8")) < DETAIL_OBSERVE_STORAGE_BYTES
