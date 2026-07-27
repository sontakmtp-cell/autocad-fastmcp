"""Compatibility facade for the versioned CAD Program contract."""

from __future__ import annotations

from typing import Any

from .program import CadProgram, canonical_program, parse_cad_program


CadProgramV02 = CadProgram


def validate_cad_program(value: dict[str, Any]) -> dict[str, Any]:
    return canonical_program(parse_cad_program(value))


validate_program = validate_cad_program


__all__ = [
    "CadProgram",
    "CadProgramV02",
    "validate_cad_program",
    "validate_program",
]
