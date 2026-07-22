"""Canonical revisions, immutable local snapshots and bounded cursors."""

from __future__ import annotations

import base64
import copy
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


REVISION_SCHEMA = "cad.revision/1"
CURSOR_SCHEMA = "cad.cursor/1"
MAX_CURSOR_BYTES = 384
MAX_CURSOR_OFFSET = 2**31 - 1


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def revision_payload(
    *,
    document_identity: dict[str, Any],
    drawing: dict[str, Any],
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the versioned, order-independent drawing state used for CAS."""

    return {
        "revision_schema": REVISION_SCHEMA,
        "document_identity": copy.deepcopy(document_identity),
        "drawing": copy.deepcopy(drawing),
        "entities": sorted(copy.deepcopy(entities), key=lambda item: item["entity_id"]),
    }


def document_revision(
    *,
    document_identity: dict[str, Any],
    drawing: dict[str, Any],
    entities: list[dict[str, Any]],
) -> str:
    payload = canonical_json(
        revision_payload(
            document_identity=document_identity,
            drawing=drawing,
            entities=entities,
        )
    )
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


@dataclass(frozen=True)
class _StoredSnapshot:
    record: SnapshotRecord
    created_at: float
    size_bytes: int


class SnapshotStoreFull(ValueError):
    """A complete snapshot cannot fit inside the configured local store."""


class BoundedSnapshotStore:
    """Oldest-first, owner-scoped local storage with TTL and direct artifact lookup."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_count: int,
        max_total_bytes: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_count <= 0 or max_total_bytes <= 0:
            raise ValueError("snapshot store limits must be greater than zero")
        self.ttl_seconds = ttl_seconds
        self.max_count = max_count
        self.max_total_bytes = max_total_bytes
        self._clock = clock
        self._snapshots: OrderedDict[str, _StoredSnapshot] = OrderedDict()
        self._artifact_to_snapshot: dict[str, str] = {}
        self._total_bytes = 0

    @property
    def snapshot_count(self) -> int:
        self.cleanup()
        return len(self._snapshots)

    @property
    def total_bytes(self) -> int:
        self.cleanup()
        return self._total_bytes

    @property
    def artifact_count(self) -> int:
        self.cleanup()
        return len(self._artifact_to_snapshot)

    def add(self, record: SnapshotRecord) -> None:
        now = self._clock()
        self.cleanup(now=now)
        owned = _copy_record(record)
        if owned.snapshot_id in self._snapshots:
            raise ValueError("snapshot ID already exists")
        if owned.artifact_id and owned.artifact_id in self._artifact_to_snapshot:
            raise ValueError("artifact ID already exists")
        size_bytes = _record_size(owned)
        if size_bytes > self.max_total_bytes:
            raise SnapshotStoreFull("snapshot exceeds the local store byte limit")
        while self._snapshots and (
            len(self._snapshots) >= self.max_count
            or self._total_bytes + size_bytes > self.max_total_bytes
        ):
            self._evict(next(iter(self._snapshots)))
        if len(self._snapshots) >= self.max_count or self._total_bytes + size_bytes > self.max_total_bytes:
            raise SnapshotStoreFull("snapshot cannot fit in the local store")
        self._snapshots[owned.snapshot_id] = _StoredSnapshot(owned, now, size_bytes)
        self._total_bytes += size_bytes
        if owned.artifact_id:
            self._artifact_to_snapshot[owned.artifact_id] = owned.snapshot_id

    def get_snapshot(self, snapshot_id: str, owner_subject: str) -> SnapshotRecord | None:
        self.cleanup()
        stored = self._snapshots.get(snapshot_id)
        if stored is None or stored.record.owner_subject != owner_subject:
            return None
        return _copy_record(stored.record)

    def get_artifact(self, artifact_id: str, owner_subject: str) -> bytes | None:
        self.cleanup()
        snapshot_id = self._artifact_to_snapshot.get(artifact_id)
        if snapshot_id is None:
            return None
        stored = self._snapshots.get(snapshot_id)
        if (
            stored is None
            or stored.record.owner_subject != owner_subject
            or stored.record.artifact_id != artifact_id
            or stored.record.artifact_bytes is None
        ):
            return None
        return bytes(stored.record.artifact_bytes)

    def cleanup(self, *, now: float | None = None) -> int:
        current = self._clock() if now is None else now
        expired = [
            snapshot_id
            for snapshot_id, stored in self._snapshots.items()
            if current - stored.created_at >= self.ttl_seconds
        ]
        for snapshot_id in expired:
            self._evict(snapshot_id)
        return len(expired)

    def clear(self) -> None:
        self._snapshots.clear()
        self._artifact_to_snapshot.clear()
        self._total_bytes = 0

    def _evict(self, snapshot_id: str) -> None:
        stored = self._snapshots.pop(snapshot_id, None)
        if stored is None:
            return
        self._total_bytes -= stored.size_bytes
        if stored.record.artifact_id:
            self._artifact_to_snapshot.pop(stored.record.artifact_id, None)


def _copy_record(record: SnapshotRecord) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=record.snapshot_id,
        owner_subject=record.owner_subject,
        device_id=record.device_id,
        document_revision=record.document_revision,
        observation_level=record.observation_level,
        drawing=copy.deepcopy(record.drawing),
        entity_summary=copy.deepcopy(record.entity_summary),
        entities=tuple(copy.deepcopy(record.entities)),
        artifact_id=record.artifact_id,
        artifact_bytes=bytes(record.artifact_bytes) if record.artifact_bytes is not None else None,
    )


def _record_size(record: SnapshotRecord) -> int:
    json_bytes = canonical_json(
        {
            "snapshot_id": record.snapshot_id,
            "owner_subject": record.owner_subject,
            "device_id": record.device_id,
            "document_revision": record.document_revision,
            "observation_level": record.observation_level,
            "drawing": record.drawing,
            "entity_summary": record.entity_summary,
            "entities": record.entities,
            "artifact_id": record.artifact_id,
        }
    ).encode("utf-8")
    return len(json_bytes) + len(record.artifact_bytes or b"")


def encode_cursor(
    *, snapshot_id: str, types: list[str], layers: list[str], offset: int
) -> str:
    payload = {
        "cursor_schema": CURSOR_SCHEMA,
        "snapshot_id": snapshot_id,
        "filter_hash": cursor_filter_hash(types, layers),
        "offset": offset,
    }
    encoded = base64.urlsafe_b64encode(canonical_json(payload).encode("utf-8"))
    return encoded.rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise ValueError("invalid cursor")
    try:
        encoded = cursor.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        if len(raw) > MAX_CURSOR_BYTES:
            raise ValueError("invalid cursor")
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid cursor") from error
    if not isinstance(value, dict) or set(value) != {
        "cursor_schema",
        "snapshot_id",
        "filter_hash",
        "offset",
    }:
        raise ValueError("invalid cursor")
    if value["cursor_schema"] != CURSOR_SCHEMA:
        raise ValueError("invalid cursor")
    if not isinstance(value["snapshot_id"], str) or not value["snapshot_id"]:
        raise ValueError("invalid cursor")
    if (
        not isinstance(value["filter_hash"], str)
        or len(value["filter_hash"]) != 64
        or any(character not in "0123456789abcdef" for character in value["filter_hash"])
    ):
        raise ValueError("invalid cursor")
    offset = value["offset"]
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= MAX_CURSOR_OFFSET:
        raise ValueError("invalid cursor")
    return value


def cursor_filter_hash(types: list[str], layers: list[str]) -> str:
    payload = canonical_json({"types": types, "layers": layers}).encode("utf-8")
    return sha256(payload).hexdigest()
