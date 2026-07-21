"""Regression tests for the shared FastMCP tool registration and routing."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from cad_core import CadServiceResponse, CommandResult

from autocad_mcp import auto_dimension_tool, phase1_dimension_perf
from autocad_mcp import phase2_dimension_activex, phase3_dimension_scope
from autocad_mcp.dimension_plans import DimensionPlanStore
from autocad_mcp import server


EXPECTED_TOOLS = {
    "drawing",
    "entity",
    "layer",
    "block",
    "annotation",
    "pid",
    "view",
    "system",
    "annotation_detect_parts",
    "annotation_plan_dimensions",
    "annotation_commit_dimension_plan",
    "annotation_auto_dimension",
    "annotation_batch_create_dimensions",
    "annotation_dimension_profiles",
    "annotation_audit_dimensions",
    "annotation_repair_dimension_layout",
}

LEGACY_DESCRIPTOR_SNAPSHOT = {
    "annotation": "bfbf0e910b1bdf17392c7b43ed1aa5ebed6737f632c9d7dba8302058917346cb",
    "annotation_audit_dimensions": "d0731d5f9c36268e0594a719d051807316d91ef46dc32916072d32a348c8718d",
    "annotation_auto_dimension": "d0bc475d7b13c7607f13c4288141549cadeed1f354764ba3572d854c73878a97",
    "annotation_batch_create_dimensions": "807d186a243e7f32db2acce070e2af3ebaf71885f78b2dd1390a01c50eb1cd86",
    "annotation_commit_dimension_plan": "8dbb212614a828159798e6109080d6f83ee5762426dbd07806154822cc31ec76",
    "annotation_detect_parts": "b804734e8256e78c2f2c94e46b24f838825c4b39929db924fdf30183c4bb980a",
    "annotation_dimension_profiles": "66a198e8840f70beab0e8e012457d1bac5349a5335211d9bb27e948b6ffd9e55",
    "annotation_plan_dimensions": "22a8be22d5fc4971017c99521e5f440d67df07cff316debafbaf0a79e8f012a1",
    "annotation_repair_dimension_layout": "2c33a082cfe8a5c53fe117a970804a432bfcf04ad78a7fed7c83b50ddd1ace6a",
    "block": "375cffeac40545719a6e1b4ccf76e7341c5ec0a9253fc2e64049323f188f21be",
    "drawing": "b647bc17db26d7146f9af38f882b6d631fb1c72bce79e9aa14c53b92614e054a",
    "entity": "44b29fe892349e77320396ade521a619a4d5bdccde7e71becaf16e38108db9f1",
    "layer": "4c275498227bda85d6b45dad49467b317e7f81a4827441918144452d3701de41",
    "pid": "ba67a209e77f77f2bc19b63403d69eaffc81b32f89ba58aec2f3c46edf9a1be4",
    "system": "2d99a5812821fb75423597d48b94fa9ffddf773094c88f336235d3235fb99a41",
    "view": "e76d6436a36b4a7ec643d69977de62822f51066c892ac2e14389d9cdb5d0cbbf",
}


def test_shared_registration_imports_and_installs_all_dimension_phases():
    status = server.register_optional_features()
    registered = set(server.mcp._tool_manager._tools)

    assert EXPECTED_TOOLS.issubset(registered)
    assert status == {
        "auto_dimension_tool_imported": True,
        "phase1_dimension_perf_installed": True,
        "phase2_dimension_activex_installed": True,
        "phase3_dimension_scope_installed": True,
    }
    assert phase1_dimension_perf._INSTALLED is True
    assert phase2_dimension_activex._INSTALLED is True
    assert phase3_dimension_scope._INSTALLED is True
    assert auto_dimension_tool._run_annotation is not None


def test_legacy_tool_descriptors_match_the_frozen_snapshot():
    server.register_optional_features()
    actual = {}
    for name, tool in server.mcp._tool_manager._tools.items():
        annotations = getattr(tool, "annotations", None)
        descriptor = {
            "name": tool.name,
            "description": " ".join((tool.description or "").split()),
            "parameters": tool.parameters,
            "annotations": {
                key: getattr(annotations, key, None)
                for key in (
                    "title",
                    "readOnlyHint",
                    "destructiveHint",
                    "idempotentHint",
                    "openWorldHint",
                )
            },
        }
        encoded = json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        actual[name] = hashlib.sha256(encoded).hexdigest()

    assert actual == LEGACY_DESCRIPTOR_SNAPSHOT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["detect_parts", "plan_dimensions", "commit_dimension_plan", "auto_dimension", "batch_create_dimensions", "dimension_profiles", "audit_dimensions", "repair_dimension_layout"],
)
async def test_unified_annotation_routes_advanced_operations_to_run_annotation(
    operation, monkeypatch
):
    server.register_optional_features()
    calls = []

    async def fake_run_annotation(*, operation, data, include_image):
        calls.append((operation, data, include_image))
        return CadServiceResponse(CommandResult(ok=True, payload={"routed": True}))

    monkeypatch.setattr(auto_dimension_tool, "_run_annotation", fake_run_annotation)

    result = await server.annotation(
        operation=operation,
        data={"profile": "mechanical_mm"},
        include_screenshot=True,
    )

    assert result == '{"ok":true,"payload":{"routed":true}}'
    assert calls == [(operation, {"profile": "mechanical_mm"}, True)]


def test_normalized_dimension_result_preserves_commit_metadata():
    committed = SimpleNamespace(
        commit_result={
            "backend": "file_ipc",
            "commit_engine": "activex",
            "dimensions_created": 3,
            "regen_count": 1,
            "undo_group": "single",
        },
        target={"entity_ids": ["A", "B"]},
        dimensions=[
            SimpleNamespace(kind="linear"),
            SimpleNamespace(kind="diameter"),
            SimpleNamespace(kind="center"),
        ],
    )

    result = auto_dimension_tool._normalized_dimension_commit_result(
        committed=committed,
        backend=SimpleNamespace(name="file_ipc"),
        context={
            "records": [object(), object()],
            "export_metrics": {
                "selection_scope": "handles",
                "scanned_count": 4,
                "exported_count": 2,
            },
        },
        timings={
            "export_geometry": 1.0,
            "detect_parts": 2.0,
            "build_candidates": 3.0,
            "commit": 4.0,
            "total": 5.0,
        },
    )

    assert result["created_count"] == 3
    assert result["dimension_types"] == {
        "linear": 1,
        "aligned": 0,
        "diameter": 1,
        "radius": 0,
        "angular": 0,
        "center": 1,
        "text": 0,
    }
    assert result["selection_scope"] == "handles"
    assert result["scanned_count"] == 4
    assert result["exported_count"] == 2
    assert result["commit_engine"] == "activex"
    assert result["regen_count"] == 1
    assert result["timings_ms"] == {
        "scan": 1.0,
        "detect_parts": 2.0,
        "dimension": 3.0,
        "commit": 4.0,
        "total": 5.0,
    }


@pytest.mark.asyncio
async def test_batch_engine_commits_once_without_low_level_dimension_calls(monkeypatch):
    class FakeBackend:
        name = "ezdxf"
        _doc = object()

    backend = FakeBackend()
    commit_calls = []

    async def fake_get_backend():
        return backend

    async def fake_commit_dimension_plan(backend_arg, plan, profile):
        commit_calls.append((backend_arg, plan.plan_id, profile.name))
        return {
            "backend": "ezdxf",
            "dimensions_created": len(plan.dimensions),
            "undo_group": "transactional_batch",
        }

    monkeypatch.setattr(phase1_dimension_perf, "get_backend", fake_get_backend)
    monkeypatch.setattr(
        phase1_dimension_perf,
        "commit_dimension_plan",
        fake_commit_dimension_plan,
    )
    monkeypatch.setattr(auto_dimension_tool, "_plans", DimensionPlanStore())

    result = await phase1_dimension_perf._run_batch_create(
        {
            "dimensions": [
                {
                    "kind": "linear",
                    "x1": 0,
                    "y1": 0,
                    "x2": 100,
                    "y2": 0,
                    "dim_x": 0,
                    "dim_y": -15,
                },
                {
                    "kind": "text",
                    "x": 10,
                    "y": -20,
                    "text": "NOTE",
                },
            ]
        },
        include_image=False,
    )

    assert len(commit_calls) == 1
    assert commit_calls[0][0] is backend
    assert result.result.payload["created_count"] == 2
