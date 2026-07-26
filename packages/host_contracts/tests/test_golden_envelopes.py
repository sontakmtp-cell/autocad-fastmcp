from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
ALLOWED_OPERATIONS = {
    "host.health",
    "drawing.observe.summary",
    "entity.snapshot.page",
    "document.events.summary",
    "cad.program.preview",
    "cad.program.commit",
    "cad.program.validate",
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_schemas_are_json_schema_2020_12_and_forbid_extra_envelope_fields():
    envelope = _load(ROOT / "schemas" / "cad-host-envelope.schema.json")
    payloads = _load(ROOT / "schemas" / "cad-host-payloads.schema.json")
    message = _load(ROOT / "schemas" / "cad-host-message.schema.json")
    assert envelope["$schema"].endswith("2020-12/schema")
    assert payloads["$schema"].endswith("2020-12/schema")
    assert message["$schema"].endswith("2020-12/schema")
    assert envelope["additionalProperties"] is False
    assert set(payloads["$defs"]) == {
        "handshake",
        "handshake_result",
        "command",
        "result",
        "error",
    }


def test_golden_envelopes_have_matching_payload_hashes_and_bounded_identity():
    for path in sorted((ROOT / "golden").glob("*.json")):
        envelope = _load(path)
        assert envelope["protocol_version"] == "cad.host/1"
        assert envelope["payload_hash"] == _canonical_hash(envelope["payload"])
        assert 1 <= len(envelope["session_id"]) <= 128
        assert 1 <= len(envelope["command_id"]) <= 128
        assert 0 <= envelope["sequence"] <= 1_000_000_000


def test_command_golden_messages_only_use_explicit_read_registry():
    commands = [
        _load(path)
        for path in (ROOT / "golden").glob("*.json")
        if _load(path)["message_type"] == "command"
    ]
    assert commands
    assert {item["payload"]["operation_id"] for item in commands} <= ALLOWED_OPERATIONS
    serialized = json.dumps(commands).lower()
    for forbidden in ("script", "assembly_path", "executable", "raw_lisp", "network_url"):
        assert forbidden not in serialized
