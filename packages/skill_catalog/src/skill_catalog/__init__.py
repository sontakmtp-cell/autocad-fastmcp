"""Trusted, versioned Phase 9 skill catalog assets."""

from importlib.resources import files
from pathlib import Path


def package_root() -> Path:
    """Return the installed, fixed catalog root; never accepts caller paths."""
    return Path(str(files(__package__)))


__all__ = ["package_root"]
