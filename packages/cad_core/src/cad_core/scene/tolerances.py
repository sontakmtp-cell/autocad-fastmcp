"""Versioned deterministic tolerance and build-budget policy."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Bounds


class SceneBudgetExceeded(RuntimeError):
    code = "scene_budget_exceeded"


@dataclass(frozen=True, slots=True)
class SceneBudgets:
    max_source_entities: int = 5_000
    max_projected_bytes: int = 8 * 1024 * 1024
    max_spatial_cells: int = 50_000
    max_cells_per_node: int = 128
    max_candidates_per_node: int = 256
    max_relation_candidates: int = 250_000
    max_relations: int = 100_000
    max_contours: int = 10_000
    max_features: int = 25_000
    max_issues: int = 25_000
    max_build_seconds: float = 10.0
    max_scene_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        values = vars_from_slots(self)
        count_limits = (
            value for name, value in values.items() if name != "max_build_seconds"
        )
        if (
            isinstance(self.max_build_seconds, bool)
            or not isinstance(self.max_build_seconds, (int, float))
            or not math.isfinite(self.max_build_seconds)
            or self.max_build_seconds <= 0
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in count_limits
            )
        ):
            raise ValueError("scene budgets must be positive")
        caps = {
            "max_source_entities": 10_000,
            "max_projected_bytes": 16 * 1024 * 1024,
            "max_spatial_cells": 100_000,
            "max_cells_per_node": 256,
            "max_candidates_per_node": 512,
            "max_relation_candidates": 500_000,
            "max_relations": 200_000,
            "max_contours": 20_000,
            "max_features": 50_000,
            "max_issues": 50_000,
            "max_build_seconds": 30.0,
            "max_scene_bytes": 32 * 1024 * 1024,
        }
        if any(values[name] > cap for name, cap in caps.items()):
            raise ValueError("scene budget exceeds server hard cap")


def vars_from_slots(value: object) -> dict[str, int | float]:
    return {
        name: getattr(value, name)
        for name in value.__dataclass_fields__  # type: ignore[attr-defined]
    }


@dataclass(frozen=True, slots=True)
class ToleranceProfile:
    profile_id: str
    drawing_units: str
    absolute_floor: float
    relative_component: float
    angular: float
    endpoint: float
    radius: float
    duplicate: float
    maximum: float


def mechanical_tolerance(
    bounds: Bounds | None,
    *,
    drawing_units: str = "unitless",
) -> ToleranceProfile:
    extent = (
        max(bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y, 1.0)
        if bounds
        else 1.0
    )
    floor = 1e-6
    maximum = 1e-2
    linear = min(max(floor, extent * 1e-9), maximum)
    return ToleranceProfile(
        profile_id="mechanical-2d/1",
        drawing_units=drawing_units,
        absolute_floor=floor,
        relative_component=1e-9,
        angular=1e-6,
        endpoint=linear,
        radius=linear,
        duplicate=linear,
        maximum=maximum,
    )


def validate_tolerance(profile: ToleranceProfile) -> None:
    values = (
        profile.absolute_floor,
        profile.relative_component,
        profile.angular,
        profile.endpoint,
        profile.radius,
        profile.duplicate,
        profile.maximum,
    )
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError("tolerance values must be finite and positive")
    if max(profile.endpoint, profile.radius, profile.duplicate) > profile.maximum:
        raise ValueError("linear tolerance exceeds maximum")
