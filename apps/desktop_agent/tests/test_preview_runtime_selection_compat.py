from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from autocad_contracts import (
    CapabilityManifest,
    CommandMessage,
    RuntimeEvidence,
    canonical_payload_hash,
)

from autocad_desktop_agent.executor import AgentExecutionError, DrawingInfoExecutor
from autocad_desktop_agent.runtime.managed_dotnet import CadPortResult


PACKAGE = {
    "package_id": "autocad.lisp.drawing_info",
    "version": "3.3-c1",
    "sha256": "a" * 64,
}


class LegacyPort:
    runtime_id = "autolisp_file_ipc"


class ManagedPreviewPort:
    runtime_id = "managed_dotnet"

    async def health(self):
        return CadPortResult(
            True,
            payload={
                "active_document": "demo.dwg",
                "active_document_id": "doc-1",
            },
        )

    async def drawing_info(self):
        return CadPortResult(
            True,
            payload={
                "document_id": "doc-1",
                "document_name": "demo.dwg",
                "revision": {"revision": 7},
                "entity_count": 1,
                "layer_count": 1,
                "layers": ["0"],
                "truncated": False,
            },
        )

    async def preview_image(self, *, document_id=None, max_width=640, max_height=480):
        assert document_id == "doc-1"
        data = b"\x89PNG\r\n\x1a\npreview"
        return CadPortResult(
            True,
            payload={
                "document_id": "doc-1",
                "revision": {"revision": 7},
                "mime_type": "image/png",
                "width": 320,
                "height": 200,
                "byte_count": len(data),
                "data_base64": base64.b64encode(data).decode("ascii"),
            },
        )


def managed_selection(*, preview=True):
    capabilities = ["observe.summary"]
    if preview:
        capabilities.append("cad.observe.preview-image/1")
    manifest = CapabilityManifest.model_validate(
        {
            "schema_version": "cad.capability/1",
            "registry_version": "cad.program/0.2",
            "cad_products": [
                {
                    "product": "AutoCAD",
                    "edition": "full",
                    "release_year": 2025,
                    "runtime": {
                        "id": "managed_dotnet",
                        "role": "primary",
                        "host_family": "R25",
                        "host_version": "0.8.0",
                        "package_id": "autocad.managed_host.r25",
                        "package_version": "0.8.0",
                        "package_hash": f"sha256:{'b' * 64}",
                    },
                    "capabilities": capabilities,
                }
            ],
        }
    )
    return SimpleNamespace(
        adapter=ManagedPreviewPort(),
        manifest=manifest,
        evidence=RuntimeEvidence(
            id="managed_dotnet",
            role="primary",
            host_family="R25",
            host_version="0.8.0",
            package_id="autocad.managed_host.r25",
            package_version="0.8.0",
            package_hash=f"sha256:{'b' * 64}",
        ),
        degraded=False,
        degradation_reason=None,
    )


class Broker:
    def __init__(self, selection):
        self.selection = selection
        self.select_calls = 0
        self.describe_calls = 0

    async def select_read_runtime(self):
        self.select_calls += 1
        return SimpleNamespace(
            adapter=LegacyPort(),
            evidence=RuntimeEvidence(id="autolisp_file_ipc", role="compatibility_fallback"),
            degraded=True,
            degradation_reason="managed_host_unavailable",
        )

    async def describe_managed_runtime(self):
        self.describe_calls += 1
        return self.selection


def preview_command():
    payload = {
        "observation_level": "summary",
        "include_preview_image": True,
        "package": PACKAGE,
    }
    return CommandMessage(
        session_id="session-1",
        device_id="device-1",
        job_id="job-1",
        command_id="command-1",
        idempotency_key="idem-1",
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_preview_is_pinned_to_managed_runtime_instead_of_read_fallback():
    broker = Broker(managed_selection())
    executor = DrawingInfoExecutor(
        LegacyPort(),
        PACKAGE,
        "0.1.0",
        runtime_broker=broker,
    )

    result = await executor.execute(preview_command())

    assert broker.describe_calls == 1
    assert broker.select_calls == 0
    assert result["preview_image"]["mime_type"] == "image/png"
    assert result["execution_evidence"]["runtime"]["id"] == "managed_dotnet"


@pytest.mark.asyncio
async def test_preview_fails_closed_when_managed_manifest_does_not_advertise_preview():
    broker = Broker(managed_selection(preview=False))
    executor = DrawingInfoExecutor(
        LegacyPort(),
        PACKAGE,
        "0.1.0",
        runtime_broker=broker,
    )

    with pytest.raises(AgentExecutionError, match="capability_missing"):
        await executor.execute(preview_command())

    assert broker.describe_calls == 1
    assert broker.select_calls == 0
