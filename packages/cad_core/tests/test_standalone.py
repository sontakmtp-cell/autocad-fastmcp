"""Runtime proof that the CAD Core wheel has no MCP or AutoCAD dependency."""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

from cad_core import CadApplicationService, CadInvocation, CommandResult


class FakePort:
    def __init__(self) -> None:
        self.typed_calls: list[str] = []
        self.fallback_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def get_status(self) -> CommandResult:
        self.typed_calls.append("status")
        return CommandResult(ok=True, payload={"backend": "fake"})

    async def health(self) -> CommandResult:
        self.typed_calls.append("health")
        return CommandResult(ok=True, payload={"backend": "fake"})

    async def get_drawing_info(self) -> CommandResult:
        self.typed_calls.append("drawing_info")
        return CommandResult(ok=True, payload={"entity_count": 0})

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        self.typed_calls.append(f"entity_list:{layer}")
        return CommandResult(ok=True, payload={"entities": [], "count": 0})

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        self.typed_calls.append(f"entity_get:{entity_id}")
        return CommandResult(ok=True, payload={"id": entity_id})

    async def list_layers(self) -> CommandResult:
        self.typed_calls.append("layer_list")
        return CommandResult(ok=True, payload={"layers": []})

    async def get_screenshot(self) -> CommandResult:
        self.typed_calls.append("screenshot")
        return CommandResult(ok=False, error="not supported")

    async def call(self, operation: str, *args: Any) -> CommandResult:
        self.fallback_calls.append((operation, args))
        return CommandResult(ok=True, payload={"operation": operation})

    async def reinitialize(self) -> CommandResult:
        return CommandResult(ok=True, payload={"initialized": True})


def test_core_runs_typed_reads_and_compatibility_fallback_without_mcp():
    async def scenario() -> None:
        port = FakePort()
        service = CadApplicationService(port)
        drawing = await service.get_drawing_info()
        fallback = await service.execute(
            CadInvocation(
                group="drawing",
                operation="create",
                arguments={"data": {"name": "standalone"}},
            )
        )
        assert drawing.payload == {"entity_count": 0}
        assert fallback.result.payload == {"operation": "drawing_create"}
        assert port.typed_calls == ["drawing_info"]
        assert port.fallback_calls == [("drawing_create", ("standalone",))]

    asyncio.run(scenario())
    for module_name in (
        "autocad_mcp",
        "mcp",
        "fastmcp",
        "starlette",
        "win32com",
        "pythoncom",
    ):
        assert importlib.util.find_spec(module_name) is None
