"""Authenticated, owner-bound Phase 10 section cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


CURSOR_SCHEMA = "cad.scene-cursor/1"
MAX_CURSOR_LENGTH = 512


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def filter_digest(filters: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(filters)).hexdigest()


def encode_cursor(
    *,
    secret: bytes,
    owner_subject: str,
    scene_id: str,
    section: str,
    filters: dict[str, Any],
    offset: int,
    projection_version: str,
) -> str:
    if len(secret) < 32 or offset < 0:
        raise ValueError("invalid cursor configuration")
    payload = {
        "schema": CURSOR_SCHEMA,
        "owner_binding": hashlib.sha256(owner_subject.encode("utf-8")).hexdigest(),
        "scene_id": scene_id,
        "section": section,
        "filter_digest": filter_digest(filters),
        "offset": offset,
        "projection_version": projection_version,
    }
    encoded = base64.urlsafe_b64encode(_canonical(payload)).rstrip(b"=")
    signature = base64.urlsafe_b64encode(
        hmac.new(secret, encoded, hashlib.sha256).digest()
    ).rstrip(b"=")
    cursor = encoded + b"." + signature
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise ValueError("cursor too large")
    return cursor.decode("ascii")


def decode_cursor(
    cursor: str,
    *,
    secret: bytes,
    owner_subject: str,
    scene_id: str,
    section: str,
    filters: dict[str, Any],
    projection_version: str,
) -> int:
    if len(secret) < 32 or not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise ValueError("invalid cursor")
    try:
        encoded, supplied = cursor.encode("ascii").split(b".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, encoded, hashlib.sha256).digest()
        ).rstrip(b"=")
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("invalid cursor")
        padded = encoded + b"=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid cursor") from error
    expected_payload = {
        "schema": CURSOR_SCHEMA,
        "owner_binding": hashlib.sha256(owner_subject.encode("utf-8")).hexdigest(),
        "scene_id": scene_id,
        "section": section,
        "filter_digest": filter_digest(filters),
        "projection_version": projection_version,
    }
    if not isinstance(payload, dict) or any(
        payload.get(key) != value for key, value in expected_payload.items()
    ):
        raise ValueError("invalid cursor")
    offset = payload.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("invalid cursor")
    return offset
