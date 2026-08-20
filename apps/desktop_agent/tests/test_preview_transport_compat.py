from __future__ import annotations

import json

from autocad_contracts import CapabilityManifest
from autocad_desktop_agent.preview_transport_compat import (
    PREVIEW_CAPABILITY,
    _advertise_preview_capability,
    _host_canonical_json,
)


def test_host_canonical_json_allows_bounded_preview_sized_string():
    value = {"data_base64": "A" * 200_000, "mime_type": "image/png"}

    encoded = _host_canonical_json(value)

    assert json.loads(encoded) == value
    assert encoded.startswith('{"data_base64":')


def test_host_canonical_json_is_deterministic():
    first = _host_canonical_json({"b": 2, "a": "Bản vẽ"})
    second = _host_canonical_json({"a": "Bản vẽ", "b": 2})

    assert first == second
    assert first == '{"a":"Bản vẽ","b":2}'


def _manifest(capabilities: list[str]) -> CapabilityManifest:
    return CapabilityManifest.model_validate(
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


def test_preview_capability_is_projected_when_managed_host_declares_it():
    projected = _advertise_preview_capability(
        ["observe", "cad.observe.detail-provenance/1"],
        _manifest(["observe.summary", PREVIEW_CAPABILITY]),
    )

    assert PREVIEW_CAPABILITY in projected
    assert projected == sorted(set(projected))


def test_preview_capability_is_not_advertised_without_host_support():
    projected = _advertise_preview_capability(
        ["observe", "cad.observe.detail-provenance/1"],
        _manifest(["observe.summary"]),
    )

    assert PREVIEW_CAPABILITY not in projected
