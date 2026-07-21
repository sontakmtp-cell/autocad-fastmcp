"""AutoCAD MCP Server v3.1 — 8 consolidated tools with operation dispatch.

Tools: drawing, entity, layer, block, annotation, pid, view, system
"""

from __future__ import annotations

import os
import subprocess
import structlog
import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from cad_core import CadApplicationService, UnknownCadOperation

from autocad_mcp.client import (
    _json,
    _safe,
    format_service_response,
)
from autocad_mcp.cad_service import build_legacy_application_service, legacy_invocation
from autocad_mcp.config import load_transport_config

# FastMCP validates return types via Pydantic. Tools that may return
# ImageContent (screenshot) alongside TextContent need a union return type.
ToolResult = str | list
SERVER_VERSION = "3.1.0"

ADVANCED_ANNOTATION_OPERATIONS = (
    "detect_parts",
    "plan_dimensions",
    "commit_dimension_plan",
    "auto_dimension",
    "batch_create_dimensions",
    "dimension_profiles",
    "audit_dimensions",
    "repair_dimension_layout",
)

log = structlog.get_logger()

_transport_config = load_transport_config()
_oauth_runtime = None
if (
    _transport_config.transport == "streamable-http"
    and _transport_config.auth_mode == "oauth"
):
    from autocad_mcp.oauth import create_oauth_runtime

    _oauth_runtime = create_oauth_runtime(_transport_config)

mcp = FastMCP(
    "autocad-mcp",
    host=_transport_config.host,
    port=_transport_config.port,
    streamable_http_path=_transport_config.path,
    stateless_http=_transport_config.stateless_http,
    auth=_oauth_runtime.auth_settings if _oauth_runtime else None,
    token_verifier=_oauth_runtime.verifier if _oauth_runtime else None,
)

_OPTIONAL_FEATURES_REGISTERED = False
_application_service: CadApplicationService = build_legacy_application_service()


async def _legacy_execute(
    group: str,
    operation: str,
    arguments: dict,
    *,
    include_screenshot: bool = False,
    direct_screenshot: bool = False,
) -> ToolResult:
    """Run a service invocation and format it at the MCP compatibility boundary."""

    try:
        response = await _application_service.execute(
            legacy_invocation(
                group,
                operation,
                arguments,
                include_screenshot=include_screenshot,
            )
        )
    except UnknownCadOperation:
        return _json({"error": f"Unknown {group} operation: {operation}"})
    if (
        group == "system"
        and operation == "health"
        and response.result.ok
        and isinstance(response.result.payload, dict)
    ):
        return _json(response.result.payload)
    return format_service_response(response, direct_screenshot=direct_screenshot)


def register_optional_features() -> dict[str, bool]:
    """Import and install the dimension feature modules for every entrypoint."""

    global _OPTIONAL_FEATURES_REGISTERED
    if _OPTIONAL_FEATURES_REGISTERED:
        from autocad_mcp import auto_dimension_tool
        from autocad_mcp import phase1_dimension_perf
        from autocad_mcp import phase2_dimension_activex
        from autocad_mcp import phase3_dimension_scope

        return {
            "auto_dimension_tool_imported": auto_dimension_tool is not None,
            "phase1_dimension_perf_installed": phase1_dimension_perf._INSTALLED,
            "phase2_dimension_activex_installed": phase2_dimension_activex._INSTALLED,
            "phase3_dimension_scope_installed": phase3_dimension_scope._INSTALLED,
        }

    from autocad_mcp import auto_dimension_tool
    from autocad_mcp import phase1_dimension_perf
    from autocad_mcp import phase2_dimension_activex
    from autocad_mcp import phase3_dimension_scope

    phase1_dimension_perf.install()
    phase2_dimension_activex.install()
    phase3_dimension_scope.install()
    _OPTIONAL_FEATURES_REGISTERED = True
    return {
        "auto_dimension_tool_imported": auto_dimension_tool is not None,
        "phase1_dimension_perf_installed": phase1_dimension_perf._INSTALLED,
        "phase2_dimension_activex_installed": phase2_dimension_activex._INSTALLED,
        "phase3_dimension_scope_installed": phase3_dimension_scope._INSTALLED,
    }


def _runtime_entrypoint() -> str:
    configured = os.environ.get("AUTOCAD_MCP_ENTRYPOINT", "").strip()
    if configured:
        return configured
    name = Path(sys.argv[0]).name.lower()
    if name == "__main__.py":
        return "python -m autocad_mcp"
    if name == "http_server.py":
        return "python -m autocad_mcp.http_server"
    return name or "import autocad_mcp.server"


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def _tool_manifest() -> dict[str, object]:
    feature_status = register_optional_features()
    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
    registered_tools = sorted(tools) if isinstance(tools, dict) else []
    from autocad_mcp import client
    from autocad_mcp.config import detect_backend, load_transport_config

    active_backend = getattr(client._backend, "name", None)
    if active_backend is None:
        try:
            active_backend = detect_backend()
        except Exception:
            active_backend = os.environ.get("AUTOCAD_MCP_BACKEND", "auto")
    config = load_transport_config()
    return {
        "ok": True,
        "server_version": SERVER_VERSION,
        "git_commit": _git_commit(),
        "entrypoint": _runtime_entrypoint(),
        "transport": config.transport,
        "backend": active_backend,
        "registered_tools": registered_tools,
        "annotation_operations": [
            "create_text",
            "create_dimension_linear",
            "create_dimension_aligned",
            "create_dimension_angular",
            "create_dimension_radius",
            "create_leader",
            *ADVANCED_ANNOTATION_OPERATIONS,
        ],
        "advanced_annotation_operations": list(ADVANCED_ANNOTATION_OPERATIONS),
        "feature_status": feature_status,
    }


# ==========================================================================
# 1. drawing — File/drawing management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Drawing Operations", "readOnlyHint": False})
@_safe("drawing")
async def drawing(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Drawing file management.

    Operations:
      create     — Create a new empty drawing. data: {name?}
      open       — Open an existing drawing. data: {path}
      info       — Get drawing extents, entity count, layers, blocks.
      save       — Save current drawing. data: {path?} (saves to path if given, else QSAVE)
      save_as_dxf — Export as DXF. data: {path}
      plot_pdf   — Plot to PDF. data: {path}
      purge      — Purge unused objects.
      get_variables — Get system variables. data: {names: [...]}
      undo       — Undo last operation.
      redo       — Redo last undone operation.
    """
    return await _legacy_execute(
        "drawing",
        operation,
        {"data": data},
        include_screenshot=include_screenshot,
    )


# ==========================================================================
# 2. entity — Entity CRUD + modification
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Entity Operations", "readOnlyHint": False})
@_safe("entity")
async def entity(
    operation: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    points: list[list[float]] | None = None,
    layer: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Entity creation, querying, and modification.

    Create operations:
      create_line       — x1, y1, x2, y2, layer?
      create_circle     — data: {cx, cy, radius}, layer?
      create_polyline   — points: [[x,y],...], data: {closed?}, layer?
      create_rectangle  — x1, y1, x2, y2, layer?
      create_arc        — data: {cx, cy, radius, start_angle, end_angle}, layer?
      create_ellipse    — data: {cx, cy, major_x, major_y, ratio}, layer?
      create_mtext      — data: {x, y, width, text, height?}, layer?
      create_hatch      — entity_id, data: {pattern?}

    Read operations:
      list              — layer? → list entities
      count             — layer? → count entities
      get               — entity_id → entity details

    Modify operations:
      copy    — entity_id, data: {dx, dy}
      move    — entity_id, data: {dx, dy}
      rotate  — entity_id, data: {cx, cy, angle}
      scale   — entity_id, data: {cx, cy, factor}
      mirror  — entity_id, x1, y1, x2, y2
      offset  — entity_id, data: {distance}
      array   — entity_id, data: {rows, cols, row_dist, col_dist}
      fillet  — data: {id1, id2, radius}
      chamfer — data: {id1, id2, dist1, dist2}
      erase   — entity_id
    """
    return await _legacy_execute(
        "entity",
        operation,
        {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "points": points,
            "layer": layer,
            "entity_id": entity_id,
            "data": data,
        },
        include_screenshot=include_screenshot,
    )


# ==========================================================================
# 3. layer — Layer management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Layer Operations", "readOnlyHint": False})
@_safe("layer")
async def layer(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Layer creation and management.

    Operations:
      list            — List all layers with properties.
      create          — data: {name, color?, linetype?}
      set_current     — data: {name}
      set_properties  — data: {name, color?, linetype?, lineweight?}
      freeze          — data: {name}
      thaw            — data: {name}
      lock            — data: {name}
      unlock          — data: {name}
    """
    return await _legacy_execute(
        "layer",
        operation,
        {"data": data},
        include_screenshot=include_screenshot,
    )


# ==========================================================================
# 4. block — Block operations
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Block Operations", "readOnlyHint": False})
@_safe("block")
async def block(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Block definition, insertion, and attribute management.

    Operations:
      list                 — List all block definitions.
      insert               — data: {name, x, y, scale?, rotation?, block_id?}
      insert_with_attributes — data: {name, x, y, scale?, rotation?, attributes: {tag: value}}
      get_attributes       — data: {entity_id}
      update_attribute     — data: {entity_id, tag, value}
      define               — data: {name, entities: [{type, ...}]}
    """
    return await _legacy_execute(
        "block",
        operation,
        {"data": data},
        include_screenshot=include_screenshot,
    )


# ==========================================================================
# 5. annotation — Text, dimensions, leaders
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD Annotation Operations", "readOnlyHint": False})
@_safe("annotation")
async def annotation(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Annotation: text, dimensions, leaders, and automatic dimension workflows.

    Operations:
      create_text             — data: {x, y, text, height?, rotation?, layer?}
      create_dimension_linear — data: {x1, y1, x2, y2, dim_x, dim_y}
      create_dimension_aligned — data: {x1, y1, x2, y2, offset}
      create_dimension_angular — data: {cx, cy, x1, y1, x2, y2}
      create_dimension_radius — data: {cx, cy, radius, angle}
      create_leader           — data: {points: [[x,y],...], text}
      detect_parts            — read-only geometry clustering; data: {source_layers?,
                                gap_tolerance?, include_screenshot?}
      plan_dimensions         — preview a plan without editing; data: {target_part_id?,
                                entity_ids?, region?, region_mode?, selection?,
                                use_current_selection?, source_layers?, profile?,
                                dimension_layer?, include_overall?, include_features?,
                                include_holes?, include_arcs?, include_centers?,
                                clear_existing?}
      commit_dimension_plan   — commit an approved plan; data: {plan_id,
                                expected_revision, ...}
      auto_dimension          — detect, plan, and commit in one request. It accepts
                                target_part_id, entity_ids, region, region_mode,
                                selection='current' or use_current_selection,
                                source_layers, dimension_layer, profile,
                                include_overall, include_features, include_holes,
                                include_arcs, include_centers, clear_existing,
                                include_screenshot.
      batch_create_dimensions — commit data.dimensions in one request, one Undo
                                group, and one final Regen where the backend supports it.
      dimension_profiles      — data: {action: list|get|save|delete, ...}
      audit_dimensions        — read-only dimension quality audit; data: {profile?,
                                dimension_layer?, include_screenshot?}
      repair_dimension_layout — apply a fresh audit's safe repairs; data: {audit_id,
                                issue_ids?, spacing?}

    Automatic-dimension selectors are mutually exclusive: target_part_id, entity_ids,
    region, or selection='current'. Dimension results include created_count,
    dimension_types, selection_scope, scan counters, commit_engine, regen_count, and
    timings_ms when the backend can provide those values.
    """
    return await _legacy_execute(
        "annotation",
        operation,
        {"data": data},
        include_screenshot=include_screenshot,
    )


# ==========================================================================
# 6. pid — P&ID operations (CTO library)
# ==========================================================================


@mcp.tool(annotations={"title": "P&ID Operations (CTO Library)", "readOnlyHint": False})
@_safe("pid")
async def pid(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """P&ID drawing with CTO symbol library.

    Operations:
      setup_layers     — Create standard P&ID layers.
      insert_symbol    — data: {category, symbol, x, y, scale?, rotation?}
      list_symbols     — data: {category}
      draw_process_line — data: {x1, y1, x2, y2}
      connect_equipment — data: {x1, y1, x2, y2}
      add_flow_arrow   — data: {x, y, rotation?}
      add_equipment_tag — data: {x, y, tag, description?}
      add_line_number  — data: {x, y, line_num, spec}
      insert_valve     — data: {x, y, valve_type, rotation?, attributes?}
      insert_instrument — data: {x, y, instrument_type, rotation?, tag_id?, range_value?}
      insert_pump      — data: {x, y, pump_type, rotation?, attributes?}
      insert_tank      — data: {x, y, tank_type, scale?, attributes?}
    """
    return await _legacy_execute(
        "pid",
        operation,
        {"data": data},
        include_screenshot=include_screenshot,
    )


# ==========================================================================
# 7. view — Viewport and screenshot
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD View Operations", "readOnlyHint": False})
@_safe("view")
async def view(
    operation: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
) -> ToolResult:
    """Viewport control and screenshot capture.

    Operations:
      zoom_extents   — Zoom to show all entities.
      zoom_window    — Zoom to window: x1, y1, x2, y2
      get_screenshot — Capture current view as PNG image.
    """
    return await _legacy_execute(
        "view",
        operation,
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        direct_screenshot=operation == "get_screenshot",
    )


# ==========================================================================
# 8. system — Server management
# ==========================================================================


@mcp.tool(annotations={"title": "AutoCAD MCP System", "readOnlyHint": False})
@_safe("system")
async def system(
    operation: str,
    data: dict | None = None,
    include_screenshot: bool = False,
) -> ToolResult:
    """Server status and management.

    Operations:
      status        — Backend info, capabilities, health check.
      health        — Quick health check (ping backend).
      get_backend   — Return current backend name and capabilities.
      runtime       — Return process/runtime details for spawn diagnostics.
      tool_manifest — Read-only registration, entrypoint, phase, and backend diagnostics.
      init          — Re-initialize the backend.
      execute_lisp  — Execute AutoLISP code (File IPC only). data: {code}.
                      Remote profiles deny this unless AUTOCAD_MCP_ALLOW_EXECUTE_LISP=1
                      (and OAuth write scope when auth_mode=oauth).
    """
    data = data or {}

    if operation == "health":
        try:
            return await _legacy_execute("system", operation, {"data": data})
        except Exception as e:
            message = str(e)
            lowered = message.lower()
            if "window not found" in lowered or "no autocad" in lowered:
                error_code = "autocad_not_running"
            elif "active document" in lowered:
                error_code = "no_active_document"
            elif "modal" in lowered or "dialog" in lowered:
                error_code = "modal_dialog_active"
            elif "busy" in lowered or "command active" in lowered:
                error_code = "autocad_busy"
            else:
                error_code = "command_routing_failed"
            return _json({"ok": False, "error_code": error_code, "error": message})
    if operation == "runtime":
        import os
        import sys

        return _json(
            {
                "ok": True,
                "platform": sys.platform,
                "python": sys.executable,
                "cwd": os.getcwd(),
                "backend_env": os.environ.get("AUTOCAD_MCP_BACKEND", "auto"),
                "wsl_interop": bool(os.environ.get("WSL_INTEROP")),
            }
        )
    if operation == "tool_manifest":
        return _json(_tool_manifest())
    if operation == "execute_lisp" and not data.get("code"):
        return _json({"error": "data.code is required"})
    if operation in {"status", "get_backend", "init", "execute_lisp"}:
        return await _legacy_execute(
            "system",
            operation,
            {"data": data},
            include_screenshot=include_screenshot,
        )
    return _json({"error": f"Unknown system operation: {operation}"})


# ==========================================================================
# Main entry point
# ==========================================================================


def main():
    """Run the MCP server on the configured transport (stdio by default)."""
    import logging
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )

    register_optional_features()

    transport_config = load_transport_config()
    log.info(
        "autocad_mcp_starting",
        version=SERVER_VERSION,
        transport=transport_config.transport,
    )

    if transport_config.transport == "stdio":
        mcp.run(transport="stdio")
    elif transport_config.transport == "streamable-http":
        from autocad_mcp.http_server import run_http_server

        run_http_server(transport_config)
    else:
        raise RuntimeError(
            "AUTOCAD_MCP_TRANSPORT=sse is not implemented in Phase 1. "
            "Use 'stdio' or 'streamable-http'."
        )
