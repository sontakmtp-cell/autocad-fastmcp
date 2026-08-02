"""Canonical IDs and digests for scene artifacts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from typing import Any


def canonical_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {
            str(key): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [canonical_value(item) for item in value]
        return sorted(items, key=canonical_json) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical values must be finite")
        return 0.0 if value == 0 else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(domain: str, value: Any) -> str:
    body = canonical_json({"domain": domain, "payload": value}).encode()
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def stable_id(prefix: str, domain: str, value: Any) -> str:
    return f"{prefix}_{digest(domain, value)[7:]}"


def quantize(value: float, tolerance: float) -> int:
    return round(value / tolerance)
