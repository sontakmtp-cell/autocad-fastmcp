"""Compatibility adapters around the MCP-era backend and dimension code."""

from __future__ import annotations

from typing import Any

from cad_core import (
    CadApplicationService,
    CadInvocation,
    CadServiceResponse,
    CommandResult,
)

from autocad_mcp import client


class LegacyRuntimeAdapter:
    """Delegate typed reads directly and retain generic dispatch for legacy writes."""

    async def get_status(self) -> CommandResult:
        backend = await client.get_backend()
        return await backend.status()

    async def health(self) -> CommandResult:
        backend = await client.get_backend()
        return await backend.health()

    async def get_drawing_info(self) -> CommandResult:
        backend = await client.get_backend()
        return await backend.drawing_info()

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        backend = await client.get_backend()
        return await backend.entity_list(layer)

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        backend = await client.get_backend()
        return await backend.entity_get(entity_id)

    async def list_layers(self) -> CommandResult:
        backend = await client.get_backend()
        return await backend.layer_list()

    async def get_screenshot(self) -> CommandResult:
        backend = await client.get_backend()
        return await backend.get_screenshot()

    async def call(self, operation: str, *args: Any) -> CommandResult:
        """Compatibility fallback for operations not typed in Phase 1.1."""
        backend = await client.get_backend()
        method = getattr(backend, operation)
        return await method(*args)

    async def reinitialize(self) -> CommandResult:
        client._backend = None
        result = await client.get_backend()
        return await result.status()


class LegacyAdvancedAnnotationAdapter:
    """Resolve the patched dimension runner only after optional features install."""

    async def execute(
        self,
        operation: str,
        data: dict[str, Any] | None,
        include_screenshot: bool,
    ) -> CadServiceResponse:
        from autocad_mcp import server

        server.register_optional_features()
        from autocad_mcp.auto_dimension_tool import _run_annotation

        return await _run_annotation(
            operation=operation,
            data=data,
            include_image=include_screenshot,
        )


def build_legacy_application_service() -> CadApplicationService:
    """Build the one service instance shared by all legacy MCP handlers."""

    return CadApplicationService(
        runtime=LegacyRuntimeAdapter(),
        advanced_annotation=LegacyAdvancedAnnotationAdapter(),
    )


def legacy_invocation(
    group: str,
    operation: str,
    arguments: dict[str, Any],
    *,
    include_screenshot: bool = False,
) -> CadInvocation:
    """Create an invocation while keeping handler argument names explicit."""

    return CadInvocation(
        group=group,
        operation=operation,
        arguments=arguments,
        include_screenshot=include_screenshot,
    )
