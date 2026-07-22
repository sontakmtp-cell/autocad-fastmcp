from __future__ import annotations

from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8', newline='\n')


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    if new in content and old not in content:
        return
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f'{path}: expected one occurrence, found {count}: {old!r}')
    write(path, content.replace(old, new, 1))


def append_once(path: str, marker: str, content: str) -> None:
    current = read(path)
    if marker in current:
        return
    write(path, current.rstrip() + '\n\n' + content.strip() + '\n')


# Distribution metadata
write(
    'packages/cad_core/pyproject.toml',
    textwrap.dedent('''\
    [project]
    name = "autocad-cad-core"
    version = "0.1.0"
    description = "MCP-independent CAD application service contracts for autocad-mcp"
    requires-python = ">=3.10"
    dependencies = []

    [dependency-groups]
    test = [
        "pytest>=8.0,<9.0",
    ]

    [build-system]
    requires = ["hatchling"]
    build-backend = "hatchling.build"

    [tool.hatch.build.targets.wheel]
    packages = ["src/cad_core"]

    [tool.pytest.ini_options]
    testpaths = ["tests"]
    '''),
)
replace_once('pyproject.toml', '    "cad-core",\n', '    "autocad-cad-core==0.1.0",\n')
replace_once(
    'pyproject.toml',
    'cad-core = { path = "packages/cad_core", editable = false }',
    'autocad-cad-core = { path = "packages/cad_core", editable = false }',
)
for path in ('poc/fastmcp-phase0/pyproject.toml', 'services/gateway/pyproject.toml'):
    replace_once(path, '    "cad-core",\n', '    "autocad-cad-core==0.1.0",\n')
    replace_once(
        path,
        'cad-core = { path = "../../packages/cad_core", editable = false }',
        'autocad-cad-core = { path = "../../packages/cad_core", editable = false }',
    )


# Typed read port
replace_once(
    'packages/cad_core/src/cad_core/contracts.py',
    '''class CadRuntimePort(Protocol):
    """Structural port for the backend operations used by the service."""

    async def call(self, operation: str, *args: Any) -> CommandResult:
        """Call one existing backend operation by its stable method name."""

    async def reinitialize(self) -> CommandResult:
        """Reset and initialize the active runtime."""
''',
    '''class CadReadPort(Protocol):
    """Typed read-only capabilities required by the public facade and Phase 4."""

    async def get_status(self) -> CommandResult:
        """Return runtime status without string dispatch."""

    async def health(self) -> CommandResult:
        """Perform a side-effect-free runtime health check."""

    async def get_drawing_info(self) -> CommandResult:
        """Return metadata for the active drawing."""

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        """List entities, optionally restricted to one layer."""

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        """Read one entity by stable identifier."""

    async def list_layers(self) -> CommandResult:
        """List drawing layers."""

    async def get_screenshot(self) -> CommandResult:
        """Return a base64 PNG payload when supported."""


class CadRuntimePort(CadReadPort, Protocol):
    """Typed reads plus a temporary compatibility fallback for legacy writes."""

    async def call(self, operation: str, *args: Any) -> CommandResult:
        """Compatibility fallback for operations not migrated to typed methods."""

    async def reinitialize(self) -> CommandResult:
        """Reset and initialize the active runtime."""
''',
)
replace_once(
    'packages/cad_core/src/cad_core/contracts.py',
    '''        self.runtime = runtime
        self.advanced_annotation = advanced_annotation

    async def execute(self, invocation: CadInvocation) -> CadServiceResponse:
''',
    '''        self.runtime = runtime
        self.advanced_annotation = advanced_annotation

    async def get_status(self) -> CommandResult:
        """Typed status path for public facades and the future Desktop Agent."""
        return await self.runtime.get_status()

    async def health(self) -> CommandResult:
        """Typed health path with the legacy normalized success envelope."""
        result = await self.runtime.health()
        if result.ok:
            payload = result.payload if isinstance(result.payload, dict) else {}
            return CommandResult(ok=True, payload={"ok": True, **payload})
        return result

    async def get_drawing_info(self) -> CommandResult:
        """Typed drawing metadata path."""
        return await self.runtime.get_drawing_info()

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        """Typed entity-list path."""
        return await self.runtime.list_entities(layer=layer)

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        """Typed single-entity read path."""
        return await self.runtime.get_entity(entity_id=entity_id)

    async def list_layers(self) -> CommandResult:
        """Typed layer-list path."""
        return await self.runtime.list_layers()

    async def get_screenshot(self) -> CadServiceResponse:
        """Typed screenshot path with a transport-neutral attachment."""
        result = await self.runtime.get_screenshot()
        if result.ok and isinstance(result.payload, str) and result.payload:
            return CadServiceResponse(
                CommandResult(ok=True, payload={"screenshot": "attached"}),
                (CadImageAttachment(mime_type="image/png", data=result.payload),),
            )
        return CadServiceResponse(result)

    async def execute(self, invocation: CadInvocation) -> CadServiceResponse:
''',
)
replace_once('packages/cad_core/src/cad_core/contracts.py', '        screenshot = await self.runtime.call("get_screenshot")\n', '        screenshot = await self.runtime.get_screenshot()\n')
replace_once('packages/cad_core/src/cad_core/contracts.py', '            return await self.runtime.call("drawing_info")\n', '            return await self.get_drawing_info()\n')
replace_once('packages/cad_core/src/cad_core/contracts.py', '            return await self.runtime.call("entity_list", args.get("layer"))\n', '            return await self.list_entities(layer=args.get("layer"))\n')
replace_once('packages/cad_core/src/cad_core/contracts.py', '            return await self.runtime.call("entity_get", args.get("entity_id"))\n', '            return await self.get_entity(entity_id=args.get("entity_id"))\n')
replace_once('packages/cad_core/src/cad_core/contracts.py', '            return await self.runtime.call("layer_list")\n', '            return await self.list_layers()\n')
replace_once(
    'packages/cad_core/src/cad_core/contracts.py',
    '''        if operation == "get_screenshot":
            result = await self.runtime.call("get_screenshot")
            if result.ok and isinstance(result.payload, str) and result.payload:
                return CadServiceResponse(
                    CommandResult(ok=True, payload={"screenshot": "attached"}),
                    (CadImageAttachment(mime_type="image/png", data=result.payload),),
                )
            return CadServiceResponse(result)
''',
    '''        if operation == "get_screenshot":
            return await self.get_screenshot()
''',
)
replace_once(
    'packages/cad_core/src/cad_core/contracts.py',
    '''        if operation in {"status", "get_backend"}:
            return await self.runtime.call("status")
        if operation == "health":
            result = await self.runtime.call("health")
            if result.ok:
                payload = result.payload if isinstance(result.payload, dict) else {}
                return CommandResult(ok=True, payload={"ok": True, **payload})
            return result
''',
    '''        if operation in {"status", "get_backend"}:
            return await self.get_status()
        if operation == "health":
            return await self.health()
''',
)
replace_once('packages/cad_core/src/cad_core/__init__.py', '    CadInvocation,\n    CadRuntimePort,\n', '    CadInvocation,\n    CadReadPort,\n    CadRuntimePort,\n')
replace_once('packages/cad_core/src/cad_core/__init__.py', '    "CadInvocation",\n    "CadRuntimePort",\n', '    "CadInvocation",\n    "CadReadPort",\n    "CadRuntimePort",\n')


# Legacy adapter typed reads
replace_once(
    'src/autocad_mcp/cad_service.py',
    '''class LegacyRuntimeAdapter:
    """Resolve and delegate backend operations without changing their behavior."""

    async def call(self, operation: str, *args: Any) -> CommandResult:
        backend = await client.get_backend()
        method = getattr(backend, operation)
        return await method(*args)

    async def reinitialize(self) -> CommandResult:
        client._backend = None
        result = await client.get_backend()
        return await result.status()
''',
    '''class LegacyRuntimeAdapter:
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
''',
)
