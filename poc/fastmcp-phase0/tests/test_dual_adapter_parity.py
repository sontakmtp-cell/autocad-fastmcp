"""Shared-runtime parity between legacy compatibility and typed public reads."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Awaitable

import pytest
from fastmcp import Client

from cad_core import (
    CadApplicationService,
    CadInvocation,
    CadServiceResponse,
    CommandResult,
    UnknownCadOperation,
)
from fastmcp_phase0.app import build_mcp_server
from fastmcp_phase0.services import ArtifactPayload, Phase0Services, PNG_SIGNATURE


VALID_PNG = PNG_SIGNATURE + b"phase1.1"
VALID_PNG_B64 = base64.b64encode(VALID_PNG).decode("ascii")
MAX_IMAGE_BYTES = 2_000_000


@dataclass(frozen=True)
class Outcome:
    category: str
    result: dict[str, Any] | None
    attachments: tuple[bytes, ...] = ()


class SharedFakeRuntime:
    def __init__(self) -> None:
        self.mode = "ok"
        self.screenshot = VALID_PNG_B64
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fallback_calls: list[tuple[str, tuple[Any, ...]]] = []

    def reset(self) -> None:
        self.calls.clear()
        self.fallback_calls.clear()

    def _result(
        self,
        operation: str,
        payload: Any,
        args: tuple[Any, ...] = (),
    ) -> CommandResult:
        self.calls.append((operation, args))
        if self.mode == "raise":
            raise RuntimeError("shared runtime exploded")
        if self.mode == "fail":
            return CommandResult(
                ok=False,
                error="shared backend failure",
                error_code="backend_error",
            )
        return CommandResult(ok=True, payload=payload)

    async def get_status(self) -> CommandResult:
        return self._result("status", {"backend": "shared"})

    async def health(self) -> CommandResult:
        return self._result("health", {"backend": "shared", "state": "ready"})

    async def get_drawing_info(self) -> CommandResult:
        return self._result(
            "drawing_info",
            {
                "entity_count": 1,
                "layers": ["0"],
                "blocks": [],
                "dxf_version": "AC1032",
            },
        )

    async def list_entities(self, *, layer: str | None = None) -> CommandResult:
        return self._result(
            "entity_list",
            {
                "count": 1,
                "entities": [
                    {"id": "E1", "type": "LINE", "layer": layer or "0"}
                ],
            },
            (layer,),
        )

    async def get_entity(self, *, entity_id: str) -> CommandResult:
        return self._result(
            "entity_get",
            {"id": entity_id, "type": "LINE", "layer": "0"},
            (entity_id,),
        )

    async def list_layers(self) -> CommandResult:
        return self._result("layer_list", {"layers": [{"name": "0"}]})

    async def get_screenshot(self) -> CommandResult:
        return self._result("get_screenshot", self.screenshot)

    async def call(self, operation: str, *args: Any) -> CommandResult:
        self.fallback_calls.append((operation, args))
        if self.mode == "raise":
            raise RuntimeError("shared runtime exploded")
        if self.mode == "fail":
            return CommandResult(
                ok=False,
                error="shared backend failure",
                error_code="backend_error",
            )
        return CommandResult(ok=True, payload={"operation": operation, "args": args})

    async def reinitialize(self) -> CommandResult:
        return CommandResult(ok=True, payload={"initialized": True})


async def legacy_adapter(service, group, operation, arguments):
    return await service.execute(CadInvocation(group, operation, arguments))


async def public_adapter(service, group, operation, arguments):
    if (group, operation) in {("system", "status"), ("system", "get_backend")}:
        return CadServiceResponse(await service.get_status())
    if (group, operation) == ("system", "health"):
        return CadServiceResponse(await service.health())
    if (group, operation) == ("drawing", "info"):
        return CadServiceResponse(await service.get_drawing_info())
    if (group, operation) == ("entity", "list"):
        return CadServiceResponse(
            await service.list_entities(layer=arguments.get("layer"))
        )
    if (group, operation) == ("view", "get_screenshot"):
        return await service.get_screenshot()
    raise UnknownCadOperation(group, operation)


async def normalize(call: Awaitable[CadServiceResponse]) -> Outcome:
    try:
        response = await call
    except UnknownCadOperation:
        return Outcome("unknown_operation", None)
    except (KeyError, TypeError, ValueError):
        return Outcome("invalid_request", None)
    except Exception:
        return Outcome("unexpected_exception", None)
    if not response.result.ok:
        return Outcome(
            response.result.error_code or "backend_error",
            response.result.to_dict(),
        )
    attachments: list[bytes] = []
    for attachment in response.attachments:
        try:
            data = base64.b64decode(attachment.data, validate=True)
        except (binascii.Error, ValueError, TypeError):
            return Outcome("invalid_attachment", response.result.to_dict())
        if len(data) > MAX_IMAGE_BYTES:
            return Outcome("invalid_attachment", response.result.to_dict())
        attachments.append(data)
    return Outcome("ok", response.result.to_dict(), tuple(attachments))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group,operation,arguments,expected_call",
    [
        ("drawing", "info", {}, ("drawing_info", ())),
        ("entity", "list", {"layer": "0"}, ("entity_list", ("0",))),
        ("view", "get_screenshot", {}, ("get_screenshot", ())),
        ("system", "health", {}, ("health", ())),
    ],
)
async def test_domain_parity_uses_same_service_without_generic_read_dispatch(
    group,
    operation,
    arguments,
    expected_call,
):
    runtime = SharedFakeRuntime()
    service = CadApplicationService(runtime)
    legacy = await normalize(legacy_adapter(service, group, operation, arguments))
    assert runtime.calls == [expected_call]
    assert runtime.fallback_calls == []
    runtime.reset()
    public = await normalize(public_adapter(service, group, operation, arguments))
    assert runtime.calls == [expected_call]
    assert runtime.fallback_calls == []
    assert legacy == public


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,category",
    [("fail", "backend_error"), ("raise", "unexpected_exception")],
)
async def test_backend_failure_and_unexpected_exception_have_domain_parity(
    mode,
    category,
):
    runtime = SharedFakeRuntime()
    runtime.mode = mode
    service = CadApplicationService(runtime)
    legacy = await normalize(legacy_adapter(service, "drawing", "info", {}))
    runtime.reset()
    public = await normalize(public_adapter(service, "drawing", "info", {}))
    assert legacy.category == public.category == category
    assert runtime.fallback_calls == []


@pytest.mark.asyncio
async def test_health_success_and_failure_have_domain_parity():
    runtime = SharedFakeRuntime()
    service = CadApplicationService(runtime)
    success_legacy = await normalize(
        legacy_adapter(service, "system", "health", {})
    )
    runtime.reset()
    success_public = await normalize(
        public_adapter(service, "system", "health", {})
    )
    assert success_legacy == success_public
    assert success_legacy.result == {
        "ok": True,
        "payload": {"ok": True, "backend": "shared", "state": "ready"},
    }
    runtime.mode = "fail"
    runtime.reset()
    failed_legacy = await normalize(
        legacy_adapter(service, "system", "health", {})
    )
    runtime.reset()
    failed_public = await normalize(
        public_adapter(service, "system", "health", {})
    )
    assert failed_legacy.category == failed_public.category == "backend_error"


@pytest.mark.asyncio
async def test_unknown_operation_and_missing_field_do_not_call_runtime():
    runtime = SharedFakeRuntime()
    service = CadApplicationService(runtime)
    legacy_unknown = await normalize(
        legacy_adapter(service, "drawing", "unknown", {})
    )
    public_unknown = await normalize(
        public_adapter(service, "drawing", "unknown", {})
    )
    assert legacy_unknown.category == public_unknown.category == "unknown_operation"
    legacy_missing = await normalize(
        legacy_adapter(service, "drawing", "open", {"data": {}})
    )
    assert legacy_missing.category == "invalid_request"
    assert runtime.calls == []
    assert runtime.fallback_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not-base64!",
        base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode("ascii"),
    ],
    ids=["invalid-base64", "oversized"],
)
async def test_invalid_or_oversized_screenshot_is_rejected_by_both_adapters(
    payload,
):
    runtime = SharedFakeRuntime()
    runtime.screenshot = payload
    service = CadApplicationService(runtime)
    legacy = await normalize(
        legacy_adapter(service, "view", "get_screenshot", {})
    )
    assert runtime.calls == [("get_screenshot", ())]
    runtime.reset()
    public = await normalize(
        public_adapter(service, "view", "get_screenshot", {})
    )
    assert legacy.category == public.category == "invalid_attachment"
    assert legacy.attachments == public.attachments == ()
    assert runtime.calls == [("get_screenshot", ())]
    assert runtime.fallback_calls == []


@pytest.mark.asyncio
async def test_fastmcp_missing_required_field_never_reaches_shared_service():
    runtime = SharedFakeRuntime()
    service = CadApplicationService(runtime)
    phase0 = Phase0Services(application_service=service)
    phase0._initialized = True
    phase0._fixture_preview = ArtifactPayload("image/png", VALID_PNG)
    server = build_mcp_server(phase0, auth=None, stateless_http=True)
    async with Client(server) as client:
        result = await client.call_tool("cad_observe", {}, raise_on_error=False)
    assert result.is_error
    assert runtime.calls == []
    assert runtime.fallback_calls == []
