"""Gateway domain objects without transport or FastMCP dependencies."""

from .jobs import (
    EffectClass,
    InvalidJobTransition,
    JobState,
    is_terminal,
    transition_allowed,
    validate_transition,
)

__all__ = [
    "EffectClass",
    "InvalidJobTransition",
    "JobState",
    "is_terminal",
    "transition_allowed",
    "validate_transition",
]
