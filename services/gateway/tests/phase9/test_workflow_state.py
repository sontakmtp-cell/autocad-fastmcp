import pytest

from autocad_gateway.workflows.state import (
    InvalidWorkflowTransition,
    child_idempotency_key,
    validate_run_transition,
    validate_safe_retry,
    validate_step_transition,
)


def test_terminal_run_is_immutable_and_illegal_transition_is_rejected():
    validate_run_transition("created", "running")
    validate_run_transition("running", "waiting_for_trusted_approval")
    validate_run_transition("waiting_for_trusted_approval", "waiting_for_recovery")
    with pytest.raises(InvalidWorkflowTransition):
        validate_run_transition("succeeded", "running")
    with pytest.raises(InvalidWorkflowTransition):
        validate_run_transition("created", "succeeded")


def test_step_transition_table_is_bounded():
    validate_step_transition("pending", "ready")
    validate_step_transition("ready", "dispatch_pending")
    with pytest.raises(InvalidWorkflowTransition):
        validate_step_transition("pending", "succeeded")


def test_child_key_is_deterministic_and_write_unknown_never_retries():
    assert child_idempotency_key("run", "prepare", 1, "prepare") == "wf:run:prepare:1:prepare"
    validate_safe_retry(retry_class="pure", effect_class="read")
    with pytest.raises(InvalidWorkflowTransition, match="write_retry_requires_recovery"):
        validate_safe_retry(retry_class="not_started", effect_class="write", child_state="outcome_unknown")
