"""Pure Phase 9 workflow state and retry safety rules.

This module deliberately has no FastMCP or AutoCAD dependencies.
"""
from __future__ import annotations

import re
from typing import Literal

RunState = Literal[
    "created", "running", "waiting_for_user",
    "waiting_for_trusted_approval", "waiting_for_job", "waiting_for_recovery",
    "paused", "succeeded", "failed", "cancelled", "needs_attention",
]
StepState = Literal[
    "pending", "ready", "dispatch_pending", "running", "waiting", "succeeded",
    "failed", "skipped", "cancelled", "needs_attention",
]

RUN_STATES = frozenset(RunState.__args__)
STEP_STATES = frozenset(StepState.__args__)
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})
TERMINAL_STEP_STATES = frozenset({"succeeded", "failed", "skipped", "cancelled", "needs_attention"})

_RUN_TRANSITIONS = {
    "created": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"waiting_for_user", "waiting_for_trusted_approval", "waiting_for_job", "waiting_for_recovery", "paused", "succeeded", "failed", "cancelled", "needs_attention"}),
    "waiting_for_user": frozenset({"running", "paused", "cancelled", "failed", "needs_attention"}),
    "waiting_for_trusted_approval": frozenset({"waiting_for_job", "waiting_for_recovery", "running", "paused", "cancelled", "failed", "needs_attention"}),
    "waiting_for_job": frozenset({"running", "waiting_for_recovery", "paused", "succeeded", "failed", "needs_attention"}),
    "waiting_for_recovery": frozenset({"running", "waiting_for_trusted_approval", "waiting_for_job", "paused", "succeeded", "failed", "needs_attention"}),
    "paused": frozenset({"running", "cancelled", "failed", "needs_attention"}),
    "succeeded": frozenset(), "failed": frozenset(), "cancelled": frozenset(), "needs_attention": frozenset(),
}
_STEP_TRANSITIONS = {
    "pending": frozenset({"ready", "skipped", "cancelled"}),
    "ready": frozenset({"dispatch_pending", "running", "skipped", "cancelled"}),
    "dispatch_pending": frozenset({"running", "waiting", "failed", "cancelled", "needs_attention"}),
    "running": frozenset({"waiting", "succeeded", "failed", "cancelled", "needs_attention"}),
    "waiting": frozenset({"running", "succeeded", "failed", "cancelled", "needs_attention"}),
    "succeeded": frozenset(), "failed": frozenset(), "skipped": frozenset(), "cancelled": frozenset(), "needs_attention": frozenset(),
}

class InvalidWorkflowTransition(ValueError):
    pass

def is_terminal_run(state: str) -> bool:
    return state in TERMINAL_RUN_STATES

def validate_run_transition(current: str, target: str) -> None:
    if current not in RUN_STATES or target not in RUN_STATES:
        raise InvalidWorkflowTransition(f"unknown workflow state: {current} -> {target}")
    if target not in _RUN_TRANSITIONS[current]:
        raise InvalidWorkflowTransition(f"invalid workflow transition: {current} -> {target}")

def validate_step_transition(current: str, target: str) -> None:
    if current not in STEP_STATES or target not in STEP_STATES:
        raise InvalidWorkflowTransition(f"unknown workflow step state: {current} -> {target}")
    if target not in _STEP_TRANSITIONS[current]:
        raise InvalidWorkflowTransition(f"invalid workflow step transition: {current} -> {target}")

_ACTIONS = {
    "observe",
    "query",
    "plan",
    "prepare",
    "preview",
    "commit",
    "validate",
    "rollback",
}
_SCENE_ACTIONS = {"build_scene", "query_scene", "validate_scene"}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def child_idempotency_key(
    run_id: str,
    step_id: str,
    attempt: int,
    action: str,
    *,
    source_digest: str | None = None,
) -> str:
    if not run_id or not step_id or attempt < 1 or action not in _ACTIONS | _SCENE_ACTIONS:
        raise ValueError("invalid deterministic child key components")
    if action in _SCENE_ACTIONS:
        if not isinstance(source_digest, str) or not _DIGEST.fullmatch(source_digest):
            raise ValueError("scene child key requires source_digest")
        return f"wf:{run_id}:{step_id}:{attempt}:{action}:{source_digest}"
    if source_digest is not None:
        raise ValueError("source_digest is only valid for scene child keys")
    return f"wf:{run_id}:{step_id}:{attempt}:{action}"

def validate_safe_retry(*, retry_class: str, child_state: str | None = None, effect_class: str = "read") -> None:
    """Reject every replay which could issue a second write after it started."""
    if retry_class not in {"pure", "read", "metadata", "not_started"}:
        raise InvalidWorkflowTransition("retry_class_invalid")
    if effect_class == "write" and child_state in {"started", "running", "acknowledged", "outcome_unknown", "recovery", "released"}:
        raise InvalidWorkflowTransition("write_retry_requires_recovery")
