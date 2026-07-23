"""Immutable AutoLISP package provenance checks."""

from __future__ import annotations

import hashlib
from pathlib import Path


class PackageMismatch(RuntimeError):
    pass


def verify_package(path: str | Path, expected: dict[str, str]) -> dict[str, str]:
    package_path = Path(path)
    if not package_path.is_file():
        raise PackageMismatch("package_missing")
    actual = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if actual != expected["sha256"]:
        raise PackageMismatch("package_hash_mismatch")
    return dict(expected)
