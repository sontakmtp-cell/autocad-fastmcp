from __future__ import annotations

import pytest

from autocad_gateway.domain.jobs import InvalidJobTransition, validate_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("queued", "dispatched"),
        ("dispatched", "acknowledged"),
        ("acknowledged", "running"),
        ("running", "succeeded"),
        ("running", "cancel_requested"),
        ("cancel_requested", "cancelled"),
        ("reconnect_pending", "queued"),
        ("outcome_unknown", "needs_attention"),
    ],
)
def test_phase3_valid_transitions(current, target):
    validate_transition(current, target, effect_class="read", evidence=True)


@pytest.mark.parametrize(
    ("current", "target"),
    [("succeeded", "queued"), ("queued", "running"), ("outcome_unknown", "queued")],
)
def test_phase3_invalid_transitions_are_rejected(current, target):
    with pytest.raises(InvalidJobTransition):
        validate_transition(current, target, effect_class="write")


def test_started_write_like_job_cannot_be_requeued():
    with pytest.raises(InvalidJobTransition):
        validate_transition("running", "queued", effect_class="write")
