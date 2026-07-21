"""Unit tests for the MCP-independent CAD service seam."""

from __future__ import annotations

import ast
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cad_core import (
    CadApplicationService,
    CadImageAttachment,
    CadInvocation,
    CadServiceResponse,
    CommandResult,
    UnknownCadOperation,
)


PNG = base64.b64encode(b"png").decode("ascii")


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.reinitialize_calls = 0

    async def call(self, operation: str, *args: Any) -> CommandResult:
        self.calls.append((operation, args))
        if operation == "get_screenshot":
            return CommandResult(ok=True, payload=PNG)
        return CommandResult(ok=True, payload={"operation": operation, "args": args})

    async def reinitialize(self) -> CommandResult:
        self.reinitialize_calls += 1
        return CommandResult(ok=True, payload={"reinitialized": True})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group,operation,arguments,expected_method,expected_args",
    [
        ("drawing", "create", {"data": {"name": "A"}}, "drawing_create", ("A",)),
        ("drawing", "info", {}, "drawing_info", ()),
        ("drawing", "save", {"data": {}}, "drawing_save", (None,)),
        ("drawing", "save_as_dxf", {"data": {"path": "a.dxf"}}, "drawing_save_as_dxf", ("a.dxf",)),
        ("drawing", "plot_pdf", {"data": {"path": "a.pdf"}}, "drawing_plot_pdf", ("a.pdf",)),
        ("drawing", "purge", {}, "drawing_purge", ()),
        ("drawing", "get_variables", {"data": {"names": ["A"]}}, "drawing_get_variables", (["A"],)),
        ("drawing", "open", {"data": {"path": "a.dwg"}}, "drawing_open", ("a.dwg",)),
        ("drawing", "undo", {}, "undo", ()),
        ("drawing", "redo", {}, "redo", ()),
        ("entity", "create_line", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "layer": "L"}, "create_line", (1, 2, 3, 4, "L")),
        ("entity", "create_circle", {"data": {"cx": 1, "cy": 2, "radius": 3}, "layer": "L"}, "create_circle", (1, 2, 3, "L")),
        ("entity", "create_polyline", {"points": [[1, 2]], "data": {"closed": True}}, "create_polyline", ([[1, 2]], True, None)),
        ("entity", "create_rectangle", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}, "create_rectangle", (1, 2, 3, 4, None)),
        ("entity", "create_arc", {"data": {"cx": 1, "cy": 2, "radius": 3, "start_angle": 4, "end_angle": 5}}, "create_arc", (1, 2, 3, 4, 5, None)),
        ("entity", "create_ellipse", {"data": {"cx": 1, "cy": 2, "major_x": 3, "major_y": 4, "ratio": 5}}, "create_ellipse", (1, 2, 3, 4, 5, None)),
        ("entity", "create_mtext", {"data": {"x": 1, "y": 2, "width": 3, "text": "T"}}, "create_mtext", (1, 2, 3, "T", 2.5, None)),
        ("entity", "create_hatch", {"entity_id": "E", "data": {}}, "create_hatch", ("E", "ANSI31")),
        ("entity", "list", {"layer": "L"}, "entity_list", ("L",)),
        ("entity", "count", {}, "entity_count", (None,)),
        ("entity", "get", {"entity_id": "E"}, "entity_get", ("E",)),
        ("entity", "copy", {"entity_id": "E", "data": {"dx": 1, "dy": 2}}, "entity_copy", ("E", 1, 2)),
        ("entity", "move", {"entity_id": "E", "data": {"dx": 1, "dy": 2}}, "entity_move", ("E", 1, 2)),
        ("entity", "rotate", {"entity_id": "E", "data": {"cx": 1, "cy": 2, "angle": 3}}, "entity_rotate", ("E", 1, 2, 3)),
        ("entity", "scale", {"entity_id": "E", "data": {"cx": 1, "cy": 2, "factor": 3}}, "entity_scale", ("E", 1, 2, 3)),
        ("entity", "mirror", {"entity_id": "E", "x1": 1, "y1": 2, "x2": 3, "y2": 4}, "entity_mirror", ("E", 1, 2, 3, 4)),
        ("entity", "offset", {"entity_id": "E", "data": {"distance": 1}}, "entity_offset", ("E", 1)),
        ("entity", "array", {"entity_id": "E", "data": {"rows": 1, "cols": 2, "row_dist": 3, "col_dist": 4}}, "entity_array", ("E", 1, 2, 3, 4)),
        ("entity", "fillet", {"data": {"id1": "A", "id2": "B", "radius": 1}}, "entity_fillet", ("A", "B", 1)),
        ("entity", "chamfer", {"data": {"id1": "A", "id2": "B", "dist1": 1, "dist2": 2}}, "entity_chamfer", ("A", "B", 1, 2)),
        ("entity", "erase", {"entity_id": "E"}, "entity_erase", ("E",)),
        ("layer", "list", {}, "layer_list", ()),
        ("layer", "create", {"data": {"name": "L"}}, "layer_create", ("L", "white", "CONTINUOUS")),
        ("layer", "set_current", {"data": {"name": "L"}}, "layer_set_current", ("L",)),
        ("layer", "set_properties", {"data": {"name": "L"}}, "layer_set_properties", ("L", None, None, None)),
        ("layer", "freeze", {"data": {"name": "L"}}, "layer_freeze", ("L",)),
        ("layer", "thaw", {"data": {"name": "L"}}, "layer_thaw", ("L",)),
        ("layer", "lock", {"data": {"name": "L"}}, "layer_lock", ("L",)),
        ("layer", "unlock", {"data": {"name": "L"}}, "layer_unlock", ("L",)),
        ("block", "list", {}, "block_list", ()),
        ("block", "insert", {"data": {"name": "B", "x": 1, "y": 2}}, "block_insert", ("B", 1, 2, 1.0, 0.0, None)),
        ("block", "insert_with_attributes", {"data": {"name": "B", "x": 1, "y": 2}}, "block_insert_with_attributes", ("B", 1, 2, 1.0, 0.0, None)),
        ("block", "get_attributes", {"data": {"entity_id": "E"}}, "block_get_attributes", ("E",)),
        ("block", "update_attribute", {"data": {"entity_id": "E", "tag": "T", "value": "V"}}, "block_update_attribute", ("E", "T", "V")),
        ("block", "define", {"data": {"name": "B"}}, "block_define", ("B", [])),
        ("annotation", "create_text", {"data": {"x": 1, "y": 2, "text": "T"}}, "create_text", (1, 2, "T", 2.5, 0.0, None)),
        ("annotation", "create_dimension_linear", {"data": {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "dim_x": 5, "dim_y": 6}}, "create_dimension_linear", (1, 2, 3, 4, 5, 6)),
        ("annotation", "create_dimension_aligned", {"data": {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "offset": 5}}, "create_dimension_aligned", (1, 2, 3, 4, 5)),
        ("annotation", "create_dimension_angular", {"data": {"cx": 1, "cy": 2, "x1": 3, "y1": 4, "x2": 5, "y2": 6}}, "create_dimension_angular", (1, 2, 3, 4, 5, 6)),
        ("annotation", "create_dimension_radius", {"data": {"cx": 1, "cy": 2, "radius": 3, "angle": 4}}, "create_dimension_radius", (1, 2, 3, 4)),
        ("annotation", "create_leader", {"data": {"points": [[1, 2]], "text": "T"}}, "create_leader", ([[1, 2]], "T")),
        ("pid", "setup_layers", {}, "pid_setup_layers", ()),
        ("pid", "insert_symbol", {"data": {"category": "c", "symbol": "s", "x": 1, "y": 2}}, "pid_insert_symbol", ("c", "s", 1, 2, 1.0, 0.0)),
        ("pid", "list_symbols", {"data": {"category": "c"}}, "pid_list_symbols", ("c",)),
        ("pid", "draw_process_line", {"data": {"x1": 1, "y1": 2, "x2": 3, "y2": 4}}, "pid_draw_process_line", (1, 2, 3, 4)),
        ("pid", "connect_equipment", {"data": {"x1": 1, "y1": 2, "x2": 3, "y2": 4}}, "pid_connect_equipment", (1, 2, 3, 4)),
        ("pid", "add_flow_arrow", {"data": {"x": 1, "y": 2}}, "pid_add_flow_arrow", (1, 2, 0.0)),
        ("pid", "add_equipment_tag", {"data": {"x": 1, "y": 2, "tag": "T"}}, "pid_add_equipment_tag", (1, 2, "T", "")),
        ("pid", "add_line_number", {"data": {"x": 1, "y": 2, "line_num": "N", "spec": "S"}}, "pid_add_line_number", (1, 2, "N", "S")),
        ("pid", "insert_valve", {"data": {"x": 1, "y": 2, "valve_type": "V"}}, "pid_insert_valve", (1, 2, "V", 0.0, None)),
        ("pid", "insert_instrument", {"data": {"x": 1, "y": 2, "instrument_type": "I"}}, "pid_insert_instrument", (1, 2, "I", 0.0, "", "")),
        ("pid", "insert_pump", {"data": {"x": 1, "y": 2, "pump_type": "P"}}, "pid_insert_pump", (1, 2, "P", 0.0, None)),
        ("pid", "insert_tank", {"data": {"x": 1, "y": 2, "tank_type": "T"}}, "pid_insert_tank", (1, 2, "T", 1.0, None)),
        ("view", "zoom_extents", {}, "zoom_extents", ()),
        ("view", "zoom_window", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}, "zoom_window", (1, 2, 3, 4)),
        ("system", "status", {}, "status", ()),
        ("system", "get_backend", {}, "status", ()),
        ("system", "health", {}, "health", ()),
        ("system", "execute_lisp", {"data": {"code": "(princ)"}}, "execute_lisp", ("(princ)",)),
    ],
)
async def test_service_dispatches_legacy_operations(
    group, operation, arguments, expected_method, expected_args
):
    runtime = FakeRuntime()
    service = CadApplicationService(runtime)

    response = await service.execute(CadInvocation(group, operation, arguments))

    assert response.result.ok is True
    assert runtime.calls[-1] == (expected_method, expected_args)


@pytest.mark.asyncio
async def test_service_uses_runtime_reinitialize_and_keeps_neutral_screenshot():
    runtime = FakeRuntime()
    service = CadApplicationService(runtime)

    initialized = await service.execute(CadInvocation("system", "init", {}))
    screenshot = await service.execute(CadInvocation("view", "get_screenshot", {}))

    assert initialized.result.payload == {"reinitialized": True}
    assert runtime.reinitialize_calls == 1
    assert screenshot.result.payload == {"screenshot": "attached"}
    assert screenshot.attachments == (
        CadImageAttachment(mime_type="image/png", data=PNG),
    )


@pytest.mark.asyncio
async def test_service_forwards_advanced_annotation_to_the_separate_port():
    runtime = FakeRuntime()

    @dataclass
    class Advanced:
        calls: list[tuple[str, dict[str, Any] | None, bool]]

        async def execute(self, operation, data, include_screenshot):
            self.calls.append((operation, data, include_screenshot))
            return CadServiceResponse(CommandResult(ok=True, payload={"advanced": True}))

    advanced = Advanced([])
    service = CadApplicationService(runtime, advanced)
    response = await service.execute(
        CadInvocation(
            "annotation",
            "auto_dimension",
            {"data": {"target_part_id": "part_1"}},
            include_screenshot=True,
        )
    )

    assert advanced.calls == [("auto_dimension", {"target_part_id": "part_1"}, True)]
    assert response.result.payload == {"advanced": True}
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_service_unknown_operation_is_explicit():
    service = CadApplicationService(FakeRuntime())

    with pytest.raises(UnknownCadOperation, match="Unknown drawing operation: missing"):
        await service.execute(CadInvocation("drawing", "missing", {}))


def test_core_contract_has_no_transport_dependencies():
    source = Path(__file__).parents[1] / "packages" / "cad_core" / "src" / "cad_core"
    imported_modules = []
    for path in source.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules.extend(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        imported_modules.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
    for forbidden in ("mcp", "fastmcp", "starlette", "pywin32", "autocad_mcp.server"):
        assert all(
            not module.lower().startswith(forbidden)
            for module in imported_modules
        )
