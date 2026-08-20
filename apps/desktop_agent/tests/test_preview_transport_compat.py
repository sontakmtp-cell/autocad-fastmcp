from __future__ import annotations

import json

from autocad_desktop_agent.preview_transport_compat import _host_canonical_json


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
