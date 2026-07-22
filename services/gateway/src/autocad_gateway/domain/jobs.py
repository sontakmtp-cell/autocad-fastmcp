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

JOB_STATES = frozenset(
    {
        "queued",
        "dispatched",
        "acknowledged",
        "running",
        "cancel_requested",
        "reconnect_pending",
        "outcome_unknown",
        "succeeded",
        "failed",
        "cancelled",
        "needs_attention",
    }
)
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"dispatched", "cancelled", "failed"}),
    "dispatched": frozenset(
        {"acknowledged", "cancel_requested", "reconnect_pending", "outcome_unknown", "failed"}
    ),
    "acknowledged": frozenset(
        {
            "running",
            "succeeded",
            "failed",
            "cancel_requested",
            "reconnect_pending",
            "outcome_unknown",
        }
    ),
    "running": frozenset(
        {"succeeded", "failed", "cancel_requested", "outcome_unknown", "reconnect_pending"}
    ),
    "cancel_requested": frozenset(
        {"cancelled", "succeeded", "failed", "outcome_unknown", "reconnect_pending"}
    ),
    "reconnect_pending": frozenset(
        {
            "queued",
            "running",
            "outcome_unknown",
            "succeeded",
            "failed",
            "cancelled",
            "needs_attention",
        }
    ),
    "outcome_unknown": frozenset({"succeeded", "failed", "cancelled", "needs_attention"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "needs_attention": frozenset(),
}

_RECOVERY_EVIDENCE_TRANSITIONS = frozenset(
    {
        ("reconnect_pending", "queued"),
        ("reconnect_pending", "running"),
        ("reconnect_pending", "outcome_unknown"),
        ("reconnect_pending", "succeeded"),
        ("reconnect_pending", "failed"),
        ("reconnect_pending", "cancelled"),
        ("outcome_unknown", "succeeded"),
        ("outcome_unknown", "failed"),
        ("outcome_unknown", "cancelled"),
    }
)


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
    if current not in JOB_STATES or target not in JOB_STATES:
        raise InvalidJobTransition(f"unknown job state: {current} -> {target}")
    if is_terminal(current):
        raise InvalidJobTransition(f"terminal job cannot transition: {current} -> {target}")
    if not transition_allowed(current, target):
        raise InvalidJobTransition(f"invalid job transition: {current} -> {target}")
    if (current, target) in _RECOVERY_EVIDENCE_TRANSITIONS and not evidence:
        raise InvalidJobTransition(
            f"recovery transition requires reconciliation evidence: {current} -> {target}"
        )
    if (
        effect_class == "write"
        and current in {"acknowledged", "running", "cancel_requested"}
        and target == "reconnect_pending"
    ):
        raise InvalidJobTransition("started write-like jobs must become outcome_unknown")
    if (
        effect_class == "write"
        and current == "reconnect_pending"
        and target == "running"
    ):
        raise InvalidJobTransition(
            "reconciled started write-like jobs must become outcome_unknown"
        )
