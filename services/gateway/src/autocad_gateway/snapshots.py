"""Immutable in-process snapshot records and stable bounded cursors."""

from __future__ import annotations

import base64
import copy
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def document_revision(metadata: dict[str, Any], entities: list[dict[str, Any]]) -> str:
    payload = canonical_json({"drawing": metadata, "entities": entities})
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    owner_subject: str
    device_id: str
    document_revision: str
    observation_level: str
    drawing: dict[str, Any]
    entity_summary: dict[str, int]
    entities: tuple[dict[str, Any], ...]
    artifact_id: str | None = None
    artifact_bytes: bytes | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "contract_version": "cad.mcp/1.0",
            "snapshot_id": self.snapshot_id,
            "device_id": self.device_id,
            "document_revision": self.document_revision,
            "observation_level": self.observation_level,
            "drawing": copy.deepcopy(self.drawing),
            "entity_summary": copy.deepcopy(self.entity_summary),
            "entity_count": len(self.entities),
        }

    def entity_values(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(self.entities))


def encode_cursor(
    *, snapshot_id: str, types: list[str], layers: list[str], offset: int
) -> str:
    payload = {"snapshot_id": snapshot_id, "types": types, "layers": layers, "offset": offset}
    encoded = base64.urlsafe_b64encode(canonical_json(payload).encode("utf-8"))
    return encoded.rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("offset"), int):
        raise ValueError("invalid cursor")
    if value["offset"] < 0:
        raise ValueError("invalid cursor")
    return value
