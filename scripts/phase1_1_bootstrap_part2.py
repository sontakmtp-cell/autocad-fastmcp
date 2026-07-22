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


# Gateway and Phase 0 runtime adapters
runtime_block = '''@dataclass
class _BackendRuntime:
    backend: Any

    async def call(self, operation: str, *args: Any):
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self):
        return await self.backend.initialize()
'''
typed_runtime_block = '''@dataclass
class _BackendRuntime:
    backend: Any

    async def get_status(self):
        return await self.backend.status()

    async def health(self):
        return await self.backend.health()

    async def get_drawing_info(self):
        return await self.backend.drawing_info()

    async def list_entities(self, *, layer: str | None = None):
        return await self.backend.entity_list(layer)

    async def get_entity(self, *, entity_id: str):
        return await self.backend.entity_get(entity_id)

    async def list_layers(self):
        return await self.backend.layer_list()

    async def get_screenshot(self):
        return await self.backend.get_screenshot()

    async def call(self, operation: str, *args: Any):
        """Compatibility fallback for write/legacy operations."""
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self):
        return await self.backend.initialize()
'''
replace_once('services/gateway/src/autocad_gateway/services.py', runtime_block, typed_runtime_block)
replace_once('services/gateway/src/autocad_gateway/services.py', '        status = await self.backend.status()\n', '        status = await self.application_service.get_status()\n')
replace_once(
    'services/gateway/src/autocad_gateway/services.py',
    '''        drawing = await self.application_service.execute(
            CadInvocation(group="drawing", operation="info", arguments={})
        )
        entities_result = await self.application_service.execute(
            CadInvocation(group="entity", operation="list", arguments={})
        )
        if not drawing.result.ok or not entities_result.result.ok:
            raise GatewayError("backend_error")
        if not isinstance(drawing.result.payload, dict) or not isinstance(
            entities_result.result.payload, dict
        ):
            raise GatewayError("backend_error")
        drawing_payload = self._dict_payload(drawing.result.payload)
        entity_rows = self._dict_payload(entities_result.result.payload).get("entities", [])
''',
    '''        drawing_result = await self.application_service.get_drawing_info()
        entities_result = await self.application_service.list_entities()
        if not drawing_result.ok or not entities_result.ok:
            raise GatewayError("backend_error")
        if not isinstance(drawing_result.payload, dict) or not isinstance(
            entities_result.payload, dict
        ):
            raise GatewayError("backend_error")
        drawing_payload = self._dict_payload(drawing_result.payload)
        entity_rows = self._dict_payload(entities_result.payload).get("entities", [])
''',
)
replace_once(
    'services/gateway/src/autocad_gateway/services.py',
    '''                detail = await self.application_service.execute(
                    CadInvocation(
                        group="entity",
                        operation="get",
                        arguments={"entity_id": normalized["entity_id"]},
                    )
                )
                if not detail.result.ok:
                    raise GatewayError("backend_error")
                normalized = self._normalize_entity(
                    {**row, **self._dict_payload(detail.result.payload)}
                )
''',
    '''                detail = await self.application_service.get_entity(
                    entity_id=normalized["entity_id"]
                )
                if not detail.ok:
                    raise GatewayError("backend_error")
                normalized = self._normalize_entity(
                    {**row, **self._dict_payload(detail.payload)}
                )
''',
)
replace_once(
    'services/gateway/src/autocad_gateway/services.py',
    '''        layer_result = await self.application_service.execute(
            CadInvocation(group="layer", operation="list", arguments={})
        )
        if not layer_result.result.ok or not isinstance(layer_result.result.payload, dict):
            raise GatewayError("backend_error")
        layer_payload = self._dict_payload(layer_result.result.payload)
''',
    '''        layer_result = await self.application_service.list_layers()
        if not layer_result.ok or not isinstance(layer_result.payload, dict):
            raise GatewayError("backend_error")
        layer_payload = self._dict_payload(layer_result.payload)
''',
)
replace_once(
    'services/gateway/src/autocad_gateway/services.py',
    '''            screenshot = await self.application_service.execute(
                CadInvocation(group="view", operation="get_screenshot", arguments={})
            )
''',
    '''            screenshot = await self.application_service.get_screenshot()
''',
)
replace_once('services/gateway/src/autocad_gateway/services.py', '        status = await self.backend.status()\n', '        status = await self.application_service.get_status()\n')

replace_once(
    'poc/fastmcp-phase0/src/fastmcp_phase0/services.py',
    '''class _BackendRuntime:
    """Small Phase 0 runtime adapter for the shared application service."""

    def __init__(self, backend: EzdxfBackend) -> None:
        self.backend = backend

    async def call(self, operation: str, *args: Any) -> CommandResult:
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self) -> CommandResult:
        return await self.backend.initialize()
''',
    '''class _BackendRuntime:
    """Typed Phase 0 reads plus the compatibility fallback used by fixture writes."""

    def __init__(self, backend: EzdxfBackend) -> None:
        self.backend = backend

    async def get_status(self) -> CommandResult:
        return await self.backend.status()

    async def health(self) -> CommandResult:
        return await self.backend.health()

    async def get_drawing_info(self) -> CommandResult:
        return await self.backend.drawing_info()

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        return await self.backend.entity_list(layer)

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        return await self.backend.entity_get(entity_id)

    async def list_layers(self) -> CommandResult:
        return await self.backend.layer_list()

    async def get_screenshot(self) -> CommandResult:
        return await self.backend.get_screenshot()

    async def call(self, operation: str, *args: Any) -> CommandResult:
        """Compatibility fallback for fixture writes not typed in Phase 1.1."""
        return await getattr(self.backend, operation)(*args)

    async def reinitialize(self) -> CommandResult:
        return await self.backend.initialize()
''',
)
replace_once(
    'poc/fastmcp-phase0/src/fastmcp_phase0/services.py',
    '''    def __init__(self, backend: EzdxfBackend | None = None) -> None:
        self.backend = backend or EzdxfBackend()
        self.runtime = _BackendRuntime(self.backend)
        self.application_service = CadApplicationService(runtime=self.runtime)
''',
    '''    def __init__(
        self,
        backend: EzdxfBackend | None = None,
        *,
        application_service: CadApplicationService | None = None,
    ) -> None:
        self.backend = backend or EzdxfBackend()
        self.runtime = _BackendRuntime(self.backend)
        self.application_service = application_service or CadApplicationService(
            runtime=self.runtime
        )
''',
)
replace_once(
    'poc/fastmcp-phase0/src/fastmcp_phase0/services.py',
    '''        screenshot = await self._required_fixture_step(
            CadInvocation(group="view", operation="get_screenshot", arguments={}),
            "preview rendering",
        )
''',
    '''        screenshot = await self.application_service.get_screenshot()
        if not screenshot.result.ok:
            raise RuntimeError("DXF fixture initialization failed at preview rendering")
''',
)
replace_once(
    'poc/fastmcp-phase0/src/fastmcp_phase0/services.py',
    '''        drawing = await self.application_service.execute(
            CadInvocation(group="drawing", operation="info", arguments={})
        )
        if not drawing.result.ok or not isinstance(drawing.result.payload, dict):
            return None
        entities = await self.application_service.execute(
            CadInvocation(group="entity", operation="list", arguments={})
        )
        if not entities.result.ok or not isinstance(entities.result.payload, dict):
            return None
        return drawing.result.payload, entities.result.payload
''',
    '''        drawing = await self.application_service.get_drawing_info()
        if not drawing.ok or not isinstance(drawing.payload, dict):
            return None
        entities = await self.application_service.list_entities()
        if not entities.ok or not isinstance(entities.payload, dict):
            return None
        return drawing.payload, entities.payload
''',
)


# Root tests enforce typed read path
replace_once(
    'tests/test_cad_core_service.py',
    '''class FakeRuntime:
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
''',
    '''class FakeRuntime:
    TYPED_READ_NAMES = {
        "status",
        "health",
        "drawing_info",
        "entity_list",
        "entity_get",
        "layer_list",
        "get_screenshot",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fallback_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.reinitialize_calls = 0

    def _typed_result(self, operation: str, *args: Any) -> CommandResult:
        self.calls.append((operation, args))
        if operation == "get_screenshot":
            return CommandResult(ok=True, payload=PNG)
        return CommandResult(ok=True, payload={"operation": operation, "args": args})

    async def get_status(self) -> CommandResult:
        return self._typed_result("status")

    async def health(self) -> CommandResult:
        return self._typed_result("health")

    async def get_drawing_info(self) -> CommandResult:
        return self._typed_result("drawing_info")

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        return self._typed_result("entity_list", layer)

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        return self._typed_result("entity_get", entity_id)

    async def list_layers(self) -> CommandResult:
        return self._typed_result("layer_list")

    async def get_screenshot(self) -> CommandResult:
        return self._typed_result("get_screenshot")

    async def call(self, operation: str, *args: Any) -> CommandResult:
        if operation in self.TYPED_READ_NAMES:
            raise AssertionError(f"typed read used compatibility fallback: {operation}")
        self.calls.append((operation, args))
        self.fallback_calls.append((operation, args))
        return CommandResult(ok=True, payload={"operation": operation, "args": args})

    async def reinitialize(self) -> CommandResult:
        self.reinitialize_calls += 1
        return CommandResult(ok=True, payload={"reinitialized": True})
''',
)
replace_once(
    'tests/test_cad_core_service.py',
    '''@pytest.mark.asyncio
async def test_service_uses_runtime_reinitialize_and_keeps_neutral_screenshot():
''',
    '''@pytest.mark.asyncio
async def test_phase4_read_operations_never_use_generic_string_dispatch():
    runtime = FakeRuntime()
    service = CadApplicationService(runtime)

    await service.get_status()
    await service.health()
    await service.get_drawing_info()
    await service.list_entities(layer="READ")
    await service.get_entity(entity_id="E-1")
    await service.list_layers()
    await service.get_screenshot()

    assert runtime.fallback_calls == []
    assert runtime.calls == [
        ("status", ()),
        ("health", ()),
        ("drawing_info", ()),
        ("entity_list", ("READ",)),
        ("entity_get", ("E-1",)),
        ("layer_list", ()),
        ("get_screenshot", ()),
    ]


@pytest.mark.asyncio
async def test_service_uses_runtime_reinitialize_and_keeps_neutral_screenshot():
''',
)
