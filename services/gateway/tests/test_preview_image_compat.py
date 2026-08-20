from __future__ import annotations

import base64

import pytest

from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.preview_image_compat import _preview_bytes
from autocad_gateway.services import GatewayError


PACKAGE = {
    "package_id": "autocad.lisp.drawing_info",
    "version": "3.3-c1",
    "sha256": "a" * 64,
}
PNG = b"\x89PNG\r\n\x1a\n" + b"gateway-preview-test"


def preview_value(data: bytes = PNG) -> dict:
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "mime_type": "image/png",
        "width": 320,
        "height": 200,
        "byte_count": len(data),
        "data_base64_chunks": [encoded[:10], encoded[10:]],
    }


def test_preview_bytes_reconstructs_chunked_png():
    assert _preview_bytes(preview_value()) == PNG


def test_preview_bytes_rejects_non_png():
    with pytest.raises(GatewayError, match="preview_unavailable"):
        _preview_bytes(preview_value(b"not-a-png"))


def test_c1_observation_validator_accepts_bounded_preview_extension():
    service = object.__new__(DurableJobService)
    service.required_package = PACKAGE
    snapshot = {
        "snapshot_id": "snapshot-preview",
        "document_revision": "b" * 64,
        "observation_level": "summary",
        "drawing": {
            "document_name": "preview.dwg",
            "entity_count": 1,
            "layers": ["0"],
            "layer_count": 1,
            "truncated": False,
            "dispatcher_version": PACKAGE["version"],
            "package_id": PACKAGE["package_id"],
            "package_version": PACKAGE["version"],
        },
        "entity_summary": {"entity_count": 1, "detail_available": False},
        "entities": [],
        "revision_evidence": {
            "revision_schema": "cad.revision/1",
            "revision_strength": "summary_only",
            "commit_safe": False,
        },
    }
    result = {
        "snapshot": snapshot,
        "preview_image": preview_value(),
        "execution_evidence": {
            "agent_version": "0.1.0",
            "runtime_state": "online_idle",
            "package": PACKAGE,
        },
    }

    assert (
        service._validate_c1_observation(
            result,
            snapshot,
            expected_package=PACKAGE,
        )
        is None
    )
