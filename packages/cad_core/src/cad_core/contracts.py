"""Pure-Python contracts and dispatch service for CAD operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CommandResult:
    """Structured result envelope from a CAD runtime operation."""

    ok: bool
    payload: Any = None
    error: str | None = None
    error_code: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["payload"] = self.payload
        else:
            result["error"] = self.error
            if self.error_code:
                result["error_code"] = self.error_code
            if self.details:
                result["details"] = self.details
        return result


@dataclass
class BackendCapabilities:
    """Declares what a backend supports."""

    can_read_drawing: bool = False
    can_modify_entities: bool = False
    can_create_entities: bool = True
    can_screenshot: bool = False
    can_save: bool = False
    can_plot_pdf: bool = False
    can_zoom: bool = False
    can_query_entities: bool = False
    can_file_operations: bool = False
    can_undo: bool = False


@dataclass(frozen=True)
class CadInvocation:
    """A normalized request sent from an adapter into the CAD service."""

    group: str
    operation: str
    arguments: dict[str, Any]
    include_screenshot: bool = False


@dataclass(frozen=True)
class CadImageAttachment:
    """A transport-neutral base64 image attachment."""

    mime_type: str
    data: str


@dataclass(frozen=True)
class CadServiceResponse:
    """A runtime result plus optional transport-neutral attachments."""

    result: CommandResult
    attachments: tuple[CadImageAttachment, ...] = ()


class UnknownCadOperation(ValueError):
    """Raised when an adapter asks the service for an unknown operation."""

    def __init__(self, group: str, operation: str) -> None:
        self.group = group
        self.operation = operation
        super().__init__(f"Unknown {group} operation: {operation}")


class CadRuntimePort(Protocol):
    """Structural port for the backend operations used by the service."""

    async def call(self, operation: str, *args: Any) -> CommandResult:
        """Call one existing backend operation by its stable method name."""

    async def reinitialize(self) -> CommandResult:
        """Reset and initialize the active runtime."""


class AdvancedAnnotationPort(Protocol):
    """Seam for the existing advanced dimension workflow."""

    async def execute(
        self,
        operation: str,
        data: dict[str, Any] | None,
        include_screenshot: bool,
    ) -> CadServiceResponse:
        """Run one advanced annotation operation."""


class CadApplicationService:
    """Dispatch normalized CAD invocations without importing any MCP package."""

    _ADVANCED_ANNOTATION_OPERATIONS = frozenset(
        {
            "detect_parts",
            "plan_dimensions",
            "commit_dimension_plan",
            "auto_dimension",
            "batch_create_dimensions",
            "dimension_profiles",
            "audit_dimensions",
            "repair_dimension_layout",
        }
    )

    def __init__(
        self,
        runtime: CadRuntimePort,
        advanced_annotation: AdvancedAnnotationPort | None = None,
    ) -> None:
        self.runtime = runtime
        self.advanced_annotation = advanced_annotation

    async def execute(self, invocation: CadInvocation) -> CadServiceResponse:
        """Dispatch one invocation and optionally attach a runtime screenshot."""

        if (
            invocation.group == "annotation"
            and invocation.operation in self._ADVANCED_ANNOTATION_OPERATIONS
        ):
            if self.advanced_annotation is None:
                raise RuntimeError("Advanced annotation port is not configured")
            return await self.advanced_annotation.execute(
                invocation.operation,
                invocation.arguments.get("data"),
                invocation.include_screenshot,
            )

        if invocation.group == "drawing":
            result = await self._drawing(invocation)
        elif invocation.group == "entity":
            result = await self._entity(invocation)
        elif invocation.group == "layer":
            result = await self._layer(invocation)
        elif invocation.group == "block":
            result = await self._block(invocation)
        elif invocation.group == "annotation":
            result = await self._annotation(invocation)
        elif invocation.group == "pid":
            result = await self._pid(invocation)
        elif invocation.group == "view":
            return await self._view(invocation)
        elif invocation.group == "system":
            result = await self._system(invocation)
        else:
            raise UnknownCadOperation(invocation.group, invocation.operation)

        if not invocation.include_screenshot:
            return CadServiceResponse(result)
        screenshot = await self.runtime.call("get_screenshot")
        if screenshot.ok and isinstance(screenshot.payload, str) and screenshot.payload:
            return CadServiceResponse(
                result,
                (CadImageAttachment(mime_type="image/png", data=screenshot.payload),),
            )
        return CadServiceResponse(result)

    async def _drawing(self, invocation: CadInvocation) -> CommandResult:
        data = invocation.arguments.get("data") or {}
        operation = invocation.operation
        if operation == "create":
            return await self.runtime.call("drawing_create", data.get("name"))
        if operation == "info":
            return await self.runtime.call("drawing_info")
        if operation == "save":
            return await self.runtime.call("drawing_save", data.get("path"))
        if operation == "save_as_dxf":
            return await self.runtime.call("drawing_save_as_dxf", data["path"])
        if operation == "plot_pdf":
            return await self.runtime.call("drawing_plot_pdf", data["path"])
        if operation == "purge":
            return await self.runtime.call("drawing_purge")
        if operation == "get_variables":
            return await self.runtime.call("drawing_get_variables", data.get("names"))
        if operation == "open":
            return await self.runtime.call("drawing_open", data["path"])
        if operation == "undo":
            return await self.runtime.call("undo")
        if operation == "redo":
            return await self.runtime.call("redo")
        raise UnknownCadOperation("drawing", operation)

    async def _entity(self, invocation: CadInvocation) -> CommandResult:
        args = invocation.arguments
        data = args.get("data") or {}
        operation = invocation.operation
        if operation == "create_line":
            return await self.runtime.call("create_line", args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2"), args.get("layer"))
        if operation == "create_circle":
            return await self.runtime.call("create_circle", data["cx"], data["cy"], data["radius"], args.get("layer"))
        if operation == "create_polyline":
            return await self.runtime.call("create_polyline", args.get("points") or [], data.get("closed", False), args.get("layer"))
        if operation == "create_rectangle":
            return await self.runtime.call("create_rectangle", args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2"), args.get("layer"))
        if operation == "create_arc":
            return await self.runtime.call("create_arc", data["cx"], data["cy"], data["radius"], data["start_angle"], data["end_angle"], args.get("layer"))
        if operation == "create_ellipse":
            return await self.runtime.call("create_ellipse", data["cx"], data["cy"], data["major_x"], data["major_y"], data["ratio"], args.get("layer"))
        if operation == "create_mtext":
            return await self.runtime.call("create_mtext", data["x"], data["y"], data["width"], data["text"], data.get("height", 2.5), args.get("layer"))
        if operation == "create_hatch":
            return await self.runtime.call("create_hatch", args.get("entity_id"), data.get("pattern", "ANSI31"))
        if operation == "list":
            return await self.runtime.call("entity_list", args.get("layer"))
        if operation == "count":
            return await self.runtime.call("entity_count", args.get("layer"))
        if operation == "get":
            return await self.runtime.call("entity_get", args.get("entity_id"))
        if operation == "copy":
            return await self.runtime.call("entity_copy", args.get("entity_id"), data["dx"], data["dy"])
        if operation == "move":
            return await self.runtime.call("entity_move", args.get("entity_id"), data["dx"], data["dy"])
        if operation == "rotate":
            return await self.runtime.call("entity_rotate", args.get("entity_id"), data["cx"], data["cy"], data["angle"])
        if operation == "scale":
            return await self.runtime.call("entity_scale", args.get("entity_id"), data["cx"], data["cy"], data["factor"])
        if operation == "mirror":
            return await self.runtime.call("entity_mirror", args.get("entity_id"), args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2"))
        if operation == "offset":
            return await self.runtime.call("entity_offset", args.get("entity_id"), data["distance"])
        if operation == "array":
            return await self.runtime.call("entity_array", args.get("entity_id"), data["rows"], data["cols"], data["row_dist"], data["col_dist"])
        if operation == "fillet":
            return await self.runtime.call("entity_fillet", data["id1"], data["id2"], data["radius"])
        if operation == "chamfer":
            return await self.runtime.call("entity_chamfer", data["id1"], data["id2"], data["dist1"], data["dist2"])
        if operation == "erase":
            return await self.runtime.call("entity_erase", args.get("entity_id"))
        raise UnknownCadOperation("entity", operation)

    async def _layer(self, invocation: CadInvocation) -> CommandResult:
        data = invocation.arguments.get("data") or {}
        operation = invocation.operation
        if operation == "list":
            return await self.runtime.call("layer_list")
        if operation == "create":
            return await self.runtime.call("layer_create", data["name"], data.get("color", "white"), data.get("linetype", "CONTINUOUS"))
        if operation == "set_current":
            return await self.runtime.call("layer_set_current", data["name"])
        if operation == "set_properties":
            return await self.runtime.call("layer_set_properties", data["name"], data.get("color"), data.get("linetype"), data.get("lineweight"))
        if operation in {"freeze", "thaw", "lock", "unlock"}:
            return await self.runtime.call(f"layer_{operation}", data["name"])
        raise UnknownCadOperation("layer", operation)

    async def _block(self, invocation: CadInvocation) -> CommandResult:
        data = invocation.arguments.get("data") or {}
        operation = invocation.operation
        if operation == "list":
            return await self.runtime.call("block_list")
        if operation == "insert":
            return await self.runtime.call("block_insert", data["name"], data["x"], data["y"], data.get("scale", 1.0), data.get("rotation", 0.0), data.get("block_id"))
        if operation == "insert_with_attributes":
            return await self.runtime.call("block_insert_with_attributes", data["name"], data["x"], data["y"], data.get("scale", 1.0), data.get("rotation", 0.0), data.get("attributes"))
        if operation == "get_attributes":
            return await self.runtime.call("block_get_attributes", data["entity_id"])
        if operation == "update_attribute":
            return await self.runtime.call("block_update_attribute", data["entity_id"], data["tag"], data["value"])
        if operation == "define":
            return await self.runtime.call("block_define", data["name"], data.get("entities", []))
        raise UnknownCadOperation("block", operation)

    async def _annotation(self, invocation: CadInvocation) -> CommandResult:
        data = invocation.arguments.get("data") or {}
        operation = invocation.operation
        if operation == "create_text":
            return await self.runtime.call("create_text", data["x"], data["y"], data["text"], data.get("height", 2.5), data.get("rotation", 0.0), data.get("layer"))
        if operation == "create_dimension_linear":
            return await self.runtime.call("create_dimension_linear", data["x1"], data["y1"], data["x2"], data["y2"], data["dim_x"], data["dim_y"])
        if operation == "create_dimension_aligned":
            return await self.runtime.call("create_dimension_aligned", data["x1"], data["y1"], data["x2"], data["y2"], data["offset"])
        if operation == "create_dimension_angular":
            return await self.runtime.call("create_dimension_angular", data["cx"], data["cy"], data["x1"], data["y1"], data["x2"], data["y2"])
        if operation == "create_dimension_radius":
            return await self.runtime.call("create_dimension_radius", data["cx"], data["cy"], data["radius"], data["angle"])
        if operation == "create_leader":
            return await self.runtime.call("create_leader", data["points"], data["text"])
        raise UnknownCadOperation("annotation", operation)

    async def _pid(self, invocation: CadInvocation) -> CommandResult:
        data = invocation.arguments.get("data") or {}
        operation = invocation.operation
        if operation == "setup_layers":
            return await self.runtime.call("pid_setup_layers")
        if operation == "insert_symbol":
            return await self.runtime.call("pid_insert_symbol", data["category"], data["symbol"], data["x"], data["y"], data.get("scale", 1.0), data.get("rotation", 0.0))
        if operation == "list_symbols":
            return await self.runtime.call("pid_list_symbols", data["category"])
        if operation in {"draw_process_line", "connect_equipment"}:
            return await self.runtime.call(f"pid_{operation}", data["x1"], data["y1"], data["x2"], data["y2"])
        if operation == "add_flow_arrow":
            return await self.runtime.call("pid_add_flow_arrow", data["x"], data["y"], data.get("rotation", 0.0))
        if operation == "add_equipment_tag":
            return await self.runtime.call("pid_add_equipment_tag", data["x"], data["y"], data["tag"], data.get("description", ""))
        if operation == "add_line_number":
            return await self.runtime.call("pid_add_line_number", data["x"], data["y"], data["line_num"], data["spec"])
        if operation == "insert_valve":
            return await self.runtime.call("pid_insert_valve", data["x"], data["y"], data["valve_type"], data.get("rotation", 0.0), data.get("attributes"))
        if operation == "insert_instrument":
            return await self.runtime.call("pid_insert_instrument", data["x"], data["y"], data["instrument_type"], data.get("rotation", 0.0), data.get("tag_id", ""), data.get("range_value", ""))
        if operation == "insert_pump":
            return await self.runtime.call("pid_insert_pump", data["x"], data["y"], data["pump_type"], data.get("rotation", 0.0), data.get("attributes"))
        if operation == "insert_tank":
            return await self.runtime.call("pid_insert_tank", data["x"], data["y"], data["tank_type"], data.get("scale", 1.0), data.get("attributes"))
        raise UnknownCadOperation("pid", operation)

    async def _view(self, invocation: CadInvocation) -> CadServiceResponse:
        args = invocation.arguments
        operation = invocation.operation
        if operation == "zoom_extents":
            return CadServiceResponse(await self.runtime.call("zoom_extents"))
        if operation == "zoom_window":
            return CadServiceResponse(await self.runtime.call("zoom_window", args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2")))
        if operation == "get_screenshot":
            result = await self.runtime.call("get_screenshot")
            if result.ok and isinstance(result.payload, str) and result.payload:
                return CadServiceResponse(
                    CommandResult(ok=True, payload={"screenshot": "attached"}),
                    (CadImageAttachment(mime_type="image/png", data=result.payload),),
                )
            return CadServiceResponse(result)
        raise UnknownCadOperation("view", operation)

    async def _system(self, invocation: CadInvocation) -> CommandResult:
        data = invocation.arguments.get("data") or {}
        operation = invocation.operation
        if operation in {"status", "get_backend"}:
            return await self.runtime.call("status")
        if operation == "health":
            result = await self.runtime.call("health")
            if result.ok:
                payload = result.payload if isinstance(result.payload, dict) else {}
                return CommandResult(ok=True, payload={"ok": True, **payload})
            return result
        if operation == "init":
            return await self.runtime.reinitialize()
        if operation == "execute_lisp":
            return await self.runtime.call("execute_lisp", data["code"])
        raise UnknownCadOperation("system", operation)
