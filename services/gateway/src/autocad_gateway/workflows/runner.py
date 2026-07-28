"""Small outbox runner using typed internal ports only."""
from __future__ import annotations

from typing import Any, Protocol

from .state import validate_safe_retry

class WorkflowPort(Protocol):
    async def dispatch(self, action_kind: str, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...

class WorkflowReconciliationPort(Protocol):
    async def reconcile(self, action_kind: str, child_ref: dict[str, Any], *, idempotency_key: str) -> dict[str, Any] | None: ...

class WorkflowRunner:
    def __init__(self, repository: Any, port: WorkflowPort, *, worker_id: str) -> None:
        self.repository, self.port, self.worker_id = repository, port, worker_id

    async def run_once(self, *, lease_seconds: int = 30) -> bool:
        action = await self.repository.claim_action(self.worker_id, lease_seconds=lease_seconds)
        if action is None:
            return False
        dispatch_started = False
        try:
            validate_safe_retry(retry_class=action["retry_class"], child_state=action.get("child_state"), effect_class=action["effect_class"])
            # This durable boundary is intentionally before the port call.  A
            # process death after it cannot turn an inconclusive write into a
            # fresh dispatch.
            action = await self.repository.mark_dispatch_started(action["action_id"], self.worker_id)
            dispatch_started = True
            result = await self.port.dispatch(action["action_kind"], action["payload"], idempotency_key=action["idempotency_key"])
            await self.repository.complete_action(action["action_id"], self.worker_id, result)
        except Exception as error:
            # A port can explicitly prove an error happened before dispatch.
            # Every other error after the durable start boundary is unknown for
            # write effects and must go through recovery/reconciliation.
            if action["effect_class"] == "write" and dispatch_started:
                await self.repository.mark_action_outcome_unknown(action["action_id"], self.worker_id, type(error).__name__)
            else:
                await self.repository.fail_action(action["action_id"], self.worker_id, type(error).__name__)
        return True

    async def reconcile_restart(self) -> int:
        """Reconcile started child identities; writes are never redispatched here."""
        reclaimed = await self.repository.reclaim_expired_actions()
        lookup = getattr(self.port, "reconcile", None)
        if lookup is None:
            return reclaimed
        for action in await self.repository.list_actions_for_reconcile():
            child_ref = action.get("child_ref")
            if child_ref is None:
                continue
            outcome = await lookup(action["action_kind"], child_ref, idempotency_key=action["idempotency_key"])
            if outcome is not None:
                await self.repository.record_reconciled_outcome(action["action_id"], outcome)
        return reclaimed
