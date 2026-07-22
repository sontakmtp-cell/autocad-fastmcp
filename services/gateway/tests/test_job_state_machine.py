from __future__ import annotations

import pytest

from autocad_gateway.domain.jobs import (
    JOB_STATES,
    InvalidJobTransition,
    validate_transition,
)


BASE_TRANSITIONS = {
    ("queued", "dispatched"),
    ("queued", "cancelled"),
    ("queued", "failed"),
    ("dispatched", "acknowledged"),
    ("dispatched", "cancel_requested"),
    ("dispatched", "reconnect_pending"),
    ("dispatched", "outcome_unknown"),
    ("dispatched", "failed"),
    ("acknowledged", "running"),
    ("acknowledged", "succeeded"),
    ("acknowledged", "failed"),
    ("acknowledged", "cancel_requested"),
    ("acknowledged", "reconnect_pending"),
    ("acknowledged", "outcome_unknown"),
    ("running", "succeeded"),
    ("running", "failed"),
    ("running", "cancel_requested"),
    ("running", "reconnect_pending"),
    ("running", "outcome_unknown"),
    ("cancel_requested", "cancelled"),
    ("cancel_requested", "succeeded"),
    ("cancel_requested", "failed"),
    ("cancel_requested", "reconnect_pending"),
    ("cancel_requested", "outcome_unknown"),
    ("reconnect_pending", "queued"),
    ("reconnect_pending", "running"),
    ("reconnect_pending", "outcome_unknown"),
    ("reconnect_pending", "succeeded"),
    ("reconnect_pending", "failed"),
    ("reconnect_pending", "cancelled"),
    ("reconnect_pending", "needs_attention"),
    ("outcome_unknown", "succeeded"),
    ("outcome_unknown", "failed"),
    ("outcome_unknown", "cancelled"),
    ("outcome_unknown", "needs_attention"),
}

EVIDENCE_TRANSITIONS = {
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


def _valid_for(effect_class: str) -> set[tuple[str, str]]:
    valid = set(BASE_TRANSITIONS)
    if effect_class == "write":
        valid -= {
            ("acknowledged", "reconnect_pending"),
            ("running", "reconnect_pending"),
            ("cancel_requested", "reconnect_pending"),
            ("reconnect_pending", "running"),
        }
    return valid


@pytest.mark.parametrize("effect_class", ["read", "write"])
def test_transition_matrix_is_exhaustive(effect_class):
    expected = _valid_for(effect_class)
    for current in JOB_STATES:
        for target in JOB_STATES:
            if (current, target) in expected:
                validate_transition(
                    current,
                    target,
                    effect_class=effect_class,
                    evidence=(current, target) in EVIDENCE_TRANSITIONS,
                )
            else:
                with pytest.raises(InvalidJobTransition):
                    validate_transition(
                        current,
                        target,
                        effect_class=effect_class,
                        evidence=True,
                    )


@pytest.mark.parametrize("current,target", sorted(EVIDENCE_TRANSITIONS))
def test_recovery_transitions_require_evidence(current, target):
    with pytest.raises(InvalidJobTransition, match="requires reconciliation evidence"):
        validate_transition(current, target, effect_class="read", evidence=False)


@pytest.mark.parametrize(
    "current,target",
    [
        ("reconnect_pending", "needs_attention"),
        ("outcome_unknown", "needs_attention"),
    ],
)
def test_recovery_policy_can_escalate_without_false_terminal_evidence(current, target):
    validate_transition(current, target, effect_class="write", evidence=False)


def test_started_write_like_job_has_no_indirect_path_back_to_queue():
    for current in ("acknowledged", "running", "cancel_requested"):
        with pytest.raises(InvalidJobTransition, match="must become outcome_unknown"):
            validate_transition(
                current,
                "reconnect_pending",
                effect_class="write",
            )
        validate_transition(current, "outcome_unknown", effect_class="write")

    with pytest.raises(InvalidJobTransition, match="must become outcome_unknown"):
        validate_transition(
            "reconnect_pending",
            "running",
            effect_class="write",
            evidence=True,
        )
    validate_transition(
        "reconnect_pending",
        "outcome_unknown",
        effect_class="write",
        evidence=True,
    )


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled", "needs_attention"])
@pytest.mark.parametrize("target", sorted(JOB_STATES))
def test_terminal_states_are_immutable(terminal, target):
    with pytest.raises(InvalidJobTransition, match="terminal job cannot transition"):
        validate_transition(terminal, target, effect_class="read", evidence=True)


def test_unknown_state_is_rejected():
    with pytest.raises(InvalidJobTransition, match="unknown job state"):
        validate_transition("made_up", "queued")
