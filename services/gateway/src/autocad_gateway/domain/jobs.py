"""Durable job state machine for the Phase 3 POC."""

from __future__ import annotations

from typing import Literal


JobState = Literal[
    "queued",
    "dispatched",
    "acknowledged",
    "running",
    "succeeded",
    "failed",
    "reconnect_pending",
    "cancel_requested",
    "cancelled",
    "outcome_unknown",
    "needs_attention",
]
EffectClass = Literal["read", "write"]

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"dispatched", "cancelled", "failed", "reconnect_pending"}),
    "dispatched": frozenset(
        {"acknowledged", "cancel_requested", "reconnect_pending", "outcome_unknown", "failed"}
    ),
    "acknowledged": frozenset(
        {"running", "cancel_requested", "reconnect_pending", "outcome_unknown", "failed"}
    ),
    "running": frozenset(
        {"succeeded", "failed", "cancel_requested", "outcome_unknown", "reconnect_pending"}
    ),
    "cancel_requested": frozenset({"cancelled", "succeeded", "failed", "outcome_unknown", "reconnect_pending"}),
    "reconnect_pending": frozenset({"queued", "succeeded", "failed", "needs_attention"}),
    "outcome_unknown": frozenset({"succeeded", "failed", "needs_attention"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "needs_attention": frozenset(),
}


class InvalidJobTransition(ValueError):
    """Raised when a job transition would violate the durable state machine."""


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def transition_allowed(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def validate_transition(
    current: JobState,
    target: JobState,
    *,
    effect_class: EffectClass = "read",
    evidence: bool = False,
) -> None:
    if is_terminal(current):
        raise InvalidJobTransition(f"terminal job cannot transition: {current} -> {target}")
    if not transition_allowed(current, target):
        raise InvalidJobTransition(f"invalid job transition: {current} -> {target}")
    if current == "outcome_unknown" and target != "needs_attention" and not evidence:
        raise InvalidJobTransition("outcome_unknown requires reconciliation evidence")
    if (
        effect_class == "write"
        and current in {"acknowledged", "running"}
        and target == "queued"
    ):
        raise InvalidJobTransition("started write-like jobs cannot be retried")
