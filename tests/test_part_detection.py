"""Tests for stable part detection and geometry target selection."""

import pytest

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend
from autocad_mcp.part_detection import (
    Bounds,
    EntityRecord,
    GeometrySelection,
    detect_parts,
    records_from_ezdxf,
    select_records,
)


def _record(handle: str, bounds: tuple[float, float, float, float]) -> EntityRecord:
    return EntityRecord(handle, "LINE", "0", Bounds(*bounds), geometry=handle)


def test_detect_parts_is_stable_and_attaches_enclosed_features():
    records = [
        _record("20", (100, 0, 140, 30)),
        _record("12", (10, 8, 15, 13)),
        _record("11", (0, 0, 40, 30)),
        _record("21", (112, 8, 117, 13)),
    ]

    parts = detect_parts(records)

    assert [part.part_id for part in parts] == ["part_1", "part_2"]
    assert parts[0].entity_ids == ("11", "12")
    assert parts[1].entity_ids == ("20", "21")
    assert parts[0].to_dict()["bbox"] == {"min": [0, 0], "max": [40, 30]}


def test_detect_parts_can_bridge_a_small_explicit_gap():
    records = [_record("1", (0, 0, 10, 10)), _record("2", (10.1, 0, 20, 10))]

    assert len(detect_parts(records)) == 2
    assert len(detect_parts(records, gap_tolerance=0.11)) == 1
    with pytest.raises(ValueError, match="finite non-negative"):
        detect_parts(records, gap_tolerance=float("nan"))


def test_selection_supports_part_region_and_entity_ids():
    records = [
        _record("A", (0, 0, 20, 20)),
        _record("B", (5, 5, 8, 8)),
        _record("C", (100, 0, 120, 20)),
    ]
    parts = detect_parts(records)

    by_part = select_records(records, {"target_part_id": "part_2"}, parts=parts)
    assert [record.handle for record in by_part] == ["C"]

    by_crossing_region = select_records(records, {"region": [7, 7, 110, 10]})
    assert [record.handle for record in by_crossing_region] == ["A", "B", "C"]

    by_contained_region = select_records(
        records,
        {"target_region": {"min": [4, 4], "max": [9, 9]}, "region_mode": "contained"},
    )
    assert [record.handle for record in by_contained_region] == ["B"]

    by_ids = select_records(records, {"target_entity_ids": ["c", "A", "A"]})
    assert [record.handle for record in by_ids] == ["A", "C"]
    current = select_records(records, {"selection": "current"})
    assert [record.handle for record in current] == ["A", "B", "C"]


def test_selection_rejects_ambiguous_or_unknown_targets():
    records = [_record("A", (0, 0, 10, 10))]

    with pytest.raises(ValueError, match="only one geometry selector"):
        GeometrySelection.from_data({"target_part_id": "part_1", "entity_ids": ["A"]})
    with pytest.raises(ValueError, match="Unknown target_part_id"):
        select_records(records, {"target_part_id": "part_2"})
    with pytest.raises(ValueError, match="Unknown entity_ids: B"):
        select_records(records, {"entity_ids": ["B"]})


@pytest.mark.asyncio
async def test_records_from_ezdxf_filters_layers_and_preserves_native_entities():
    backend = EzdxfBackend()
    await backend.initialize()
    left = await backend.create_rectangle(0, 0, 40, 20, layer="PARTS")
    hole = await backend.create_circle(10, 10, 3, layer="PARTS")
    await backend.create_rectangle(100, 0, 120, 10, layer="IGNORE")
    await backend.create_mtext(0, 0, 10, "note", layer="PARTS")

    records = records_from_ezdxf(
        backend._msp,
        excluded_layers={"MCP-DIM", "DEFPOINTS"},
        source_layers={"PARTS"},
    )
    parts = detect_parts(records)

    assert {record.handle for record in records} == {
        left.payload["handle"],
        hole.payload["handle"],
    }
    assert all(record.geometry is not None for record in records)
    assert len(parts) == 1
    assert parts[0].to_dict()["entity_types"] == {"LWPOLYLINE": 1, "CIRCLE": 1}
