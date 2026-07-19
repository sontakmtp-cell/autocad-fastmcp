"""Reusable dimension-profile definitions and local JSON persistence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


_PROFILE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_VALID_UNITS = frozenset({"mm", "inch"})
_VALID_LAYOUTS = frozenset({"auto", "baseline", "ordinate", "chain"})
_VALID_TOLERANCE_MODES = frozenset({"none", "symmetric", "deviation"})


def _non_empty_text(value: object, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    if any(ord(character) < 32 for character in text):
        raise ValueError(f"{field} contains control characters")
    return text


def _positive_float(value: object, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return number


def _non_negative_float(value: object, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _boolean(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError(f"{field} must be a boolean")


@dataclass(frozen=True)
class DimensionProfile:
    """All drafting choices needed to reproduce a dimension layout."""

    name: str
    dimstyle: str
    layer: str
    units: str
    precision: int
    text_height: float
    arrow_size: float
    row_spacing: float
    scale_factor: float = 1.0
    tolerance_mode: str = "none"
    tolerance_upper: float = 0.0
    tolerance_lower: float = 0.0
    diameter_prefix: str = "Ø"
    radius_prefix: str = "R"
    quantity_format: str = "{count}x {value}"
    include_centerlines: bool = True
    preferred_layout: str = "auto"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DimensionProfile":
        name = _non_empty_text(data.get("name", ""), "name")
        if not _PROFILE_NAME.fullmatch(name):
            raise ValueError(
                "name must start with a letter and contain only letters, numbers, '-' or '_'"
            )

        units = str(data.get("units", "")).strip().lower()
        if units not in _VALID_UNITS:
            raise ValueError("units must be 'mm' or 'inch'")

        precision = int(data.get("precision", 2))
        if precision < 0 or precision > 8:
            raise ValueError("precision must be between 0 and 8")

        tolerance_mode = str(data.get("tolerance_mode", "none")).strip().lower()
        if tolerance_mode not in _VALID_TOLERANCE_MODES:
            raise ValueError("tolerance_mode must be 'none', 'symmetric' or 'deviation'")

        preferred_layout = str(data.get("preferred_layout", "auto")).strip().lower()
        if preferred_layout not in _VALID_LAYOUTS:
            raise ValueError("preferred_layout must be auto, baseline, ordinate or chain")

        quantity_format = _non_empty_text(
            data.get("quantity_format", "{count}x {value}"),
            "quantity_format",
        )
        if "{count}" not in quantity_format or "{value}" not in quantity_format:
            raise ValueError("quantity_format must contain {count} and {value}")

        return cls(
            name=name,
            dimstyle=_non_empty_text(data.get("dimstyle", "STANDARD"), "dimstyle"),
            layer=_non_empty_text(data.get("layer", "MCP-DIM"), "layer"),
            units=units,
            precision=precision,
            text_height=_positive_float(data.get("text_height", 2.5), "text_height"),
            arrow_size=_positive_float(data.get("arrow_size", 2.5), "arrow_size"),
            row_spacing=_positive_float(data.get("row_spacing", 7.5), "row_spacing"),
            scale_factor=_positive_float(data.get("scale_factor", 1.0), "scale_factor"),
            tolerance_mode=tolerance_mode,
            tolerance_upper=_non_negative_float(
                data.get("tolerance_upper", 0.0), "tolerance_upper"
            ),
            tolerance_lower=_non_negative_float(
                data.get("tolerance_lower", 0.0), "tolerance_lower"
            ),
            diameter_prefix=str(data.get("diameter_prefix", "Ø")),
            radius_prefix=str(data.get("radius_prefix", "R")),
            quantity_format=quantity_format,
            include_centerlines=_boolean(
                data.get("include_centerlines", True), "include_centerlines"
            ),
            preferred_layout=preferred_layout,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_overrides(self, overrides: Mapping[str, Any]) -> "DimensionProfile":
        """Return a validated one-call variant without modifying the stored profile."""

        merged = self.to_dict()
        merged.update(overrides)
        merged["name"] = self.name
        return self.from_dict(merged)


BUILTIN_DIMENSION_PROFILES: dict[str, DimensionProfile] = {
    "mechanical_mm": DimensionProfile.from_dict(
        {
            "name": "mechanical_mm",
            "dimstyle": "ISO-25",
            "layer": "MCP-DIM",
            "units": "mm",
            "precision": 2,
            "text_height": 2.5,
            "arrow_size": 2.5,
            "row_spacing": 7.5,
            "preferred_layout": "baseline",
        }
    ),
    "mechanical_inch": DimensionProfile.from_dict(
        {
            "name": "mechanical_inch",
            "dimstyle": "STANDARD",
            "layer": "MCP-DIM",
            "units": "inch",
            "precision": 3,
            "text_height": 0.125,
            "arrow_size": 0.125,
            "row_spacing": 0.375,
            "preferred_layout": "baseline",
        }
    ),
    "iso_simple": DimensionProfile.from_dict(
        {
            "name": "iso_simple",
            "dimstyle": "ISO-25",
            "layer": "MCP-DIM",
            "units": "mm",
            "precision": 1,
            "text_height": 2.5,
            "arrow_size": 2.5,
            "row_spacing": 7.5,
            "include_centerlines": False,
            "preferred_layout": "auto",
        }
    ),
}


class DimensionProfileStore:
    """Resolve built-ins and optionally persist custom profiles in one JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._custom: dict[str, DimensionProfile] = {}
        if path is not None and path.exists():
            self._load()

    def list_profiles(self) -> list[DimensionProfile]:
        combined = {**BUILTIN_DIMENSION_PROFILES, **self._custom}
        return [combined[name] for name in sorted(combined)]

    def get(
        self,
        name: str,
        overrides: Mapping[str, Any] | None = None,
    ) -> DimensionProfile:
        profile = self._custom.get(name) or BUILTIN_DIMENSION_PROFILES.get(name)
        if profile is None:
            raise KeyError(f"Unknown dimension profile: {name}")
        return profile.with_overrides(overrides) if overrides else profile

    def save(self, data: Mapping[str, Any], *, replace_existing: bool = False) -> DimensionProfile:
        profile = DimensionProfile.from_dict(data)
        if profile.name in BUILTIN_DIMENSION_PROFILES:
            raise ValueError(f"Built-in profile {profile.name!r} cannot be replaced")
        if profile.name in self._custom and not replace_existing:
            raise ValueError(f"Dimension profile {profile.name!r} already exists")
        self._custom[profile.name] = profile
        self._persist()
        return profile

    def delete(self, name: str) -> bool:
        if name in BUILTIN_DIMENSION_PROFILES:
            raise ValueError(f"Built-in profile {name!r} cannot be deleted")
        removed = self._custom.pop(name, None) is not None
        if removed:
            self._persist()
        return removed

    def _load(self) -> None:
        assert self._path is not None
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        profiles = raw.get("profiles", [])
        if not isinstance(profiles, list):
            raise ValueError("Dimension profile file must contain a profiles array")
        loaded = [DimensionProfile.from_dict(item) for item in profiles]
        if any(item.name in BUILTIN_DIMENSION_PROFILES for item in loaded):
            raise ValueError("Custom profile file cannot replace built-in profiles")
        self._custom = {item.name: item for item in loaded}

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "profiles": [self._custom[name].to_dict() for name in sorted(self._custom)],
        }
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._path)
