"""Safe handling for absent and invalid preview artifacts."""

from __future__ import annotations

import pytest
from cad_core import CommandResult
from fastmcp import Client

from fastmcp_phase0.app import build_mcp_server
from fastmcp_phase0.services import (
    MAX_PREVIEW_BYTES,
    PNG_SIGNATURE,
    ArtifactPayload,
    Phase0Services,
)


class PreviewScenarioServices(Phase0Services):
    def __init__(self, scenario: str) -> None:
        super().__init__()
        self.scenario = scenario

    async def observe(self, request, principal, correlation_id):
        result = await super().observe(request, principal, correlation_id)
        if not result.ok:
            return result
        if self.scenario == "empty_refs":
            result.payload["artifact_refs"] = []
        elif self.scenario == "missing_id":
            result.payload["artifact_refs"][0]["artifact_id"] = "artifact-does-not-exist"
        return result

    async def read_artifact(self, artifact_id, principal, correlation_id):
        if self.scenario in {
            "non_bytes",
            "empty_png",
            "oversized_png",
            "wrong_mime",
        }:
            self._record("cad_artifact", principal, correlation_id)
            valid_png = self._fixture_preview.data
            artifacts = {
                "non_bytes": ArtifactPayload(mime_type="image/png", data="not-bytes"),
                "empty_png": ArtifactPayload(mime_type="image/png", data=b""),
                "oversized_png": ArtifactPayload(
                    mime_type="image/png",
                    data=PNG_SIGNATURE + b"x" * MAX_PREVIEW_BYTES,
                ),
                "wrong_mime": ArtifactPayload(mime_type="text/plain", data=valid_png),
            }
            return CommandResult(ok=True, payload=artifacts[self.scenario])
        return await super().read_artifact(artifact_id, principal, correlation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "empty_refs",
        "missing_id",
        "non_bytes",
        "empty_png",
        "oversized_png",
        "wrong_mime",
    ],
)
async def test_invalid_preview_artifact_returns_safe_mcp_error(scenario):
    services = PreviewScenarioServices(scenario)
    await services.initialize()
    server = build_mcp_server(services, auth=None, stateless_http=True)

    async with Client(server) as client:
        result = await client.call_tool(
            "cad_observe",
            {
                "device_id": "cad-online-01",
                "include_preview_image": True,
            },
            raise_on_error=False,
        )

    assert result.is_error
    message = result.content[0].text
    assert "preview_unavailable" in message
    assert "IndexError" not in message
    assert "Traceback" not in message
    assert "artifact-does-not-exist" not in message
    assert all(item.type != "image" for item in result.content)
