"""Small outbox runner using typed internal ports only."""
from __future__ import annotations

from typing import Any, Protocol

from .state import validate_safe_retry

class WorkflowPort(Protocol):
    async def dispatch(self, action_kind: str, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...

class WorkflowReconciliationPort(Protocol):
    async def reconcile(self, action_kind: str, child_ref: dict[str, Any], *, idempotency_key: str) -> dict[str, Any] | None: ...

class SceneWorkflowPort(WorkflowPort, Protocol):
    async def source_digest(
        self,
        *,
        owner_subject: str,
        device_id: str,
        source_snapshot_id: str,
        document_revision: str,
        analysis_profile: str,
    ) -> str: ...

    async def reconcile(self, action_kind: str, child_ref: dict[str, Any], *, idempotency_key: str) -> dict[str, Any] | None: ...


_SCENE_ACTIONS = frozenset({"build_scene", "query_scene", "validate_scene"})


class WorkflowRunner:
    def __init__(
        self,
        repository: Any,
        port: WorkflowPort,
        *,
        worker_id: str,
        scene_port: SceneWorkflowPort | None = None,
    ) -> None:
        self.repository, self.port, self.worker_id = repository, port, worker_id
        self.scene_port = scene_port

    def set_scene_port(self, scene_port: SceneWorkflowPort) -> None:
        self.scene_port = scene_port

    async def run_once(self, *, lease_seconds: int = 30) -> bool:
        action = await self.repository.claim_action(self.worker_id, lease_seconds=lease_seconds)
        if action is None:
            return False
        dispatch_started = False
        try:
            validate_safe_retry(retry_class=action["retry_class"], child_state=action.get("child_state"), effect_class=action["effect_class"])
            preflight = getattr(self.port, "preflight", None)
            if preflight is not None:
                await preflight(action["action_kind"], action["payload"])
            dispatch_port = (
                self.scene_port
                if action["action_kind"] in _SCENE_ACTIONS
                else self.port
            )
            if dispatch_port is None:
                raise ValueError("scene_workflow_port_missing")
            scene_preflight = getattr(dispatch_port, "preflight", None)
            if scene_preflight is not None and dispatch_port is not self.port:
                await scene_preflight(action["action_kind"], action["payload"])
            # This durable boundary is intentionally before the port call.  A
            # process death after it cannot turn an inconclusive write into a
            # fresh dispatch.
            action = await self.repository.mark_dispatch_started(action["action_id"], self.worker_id)
            dispatch_started = True
            result = await dispatch_port.dispatch(
                action["action_kind"],
                action["payload"],
                idempotency_key=action["idempotency_key"],
            )
            child_ref = {
                key: result[key]
                for key in (
                    "program_id",
                    "program_revision",
                    "preview_id",
                    "intent_id",
                    "consent_id",
                    "job_id",
                    "receipt_id",
                    "recovery_id",
                    "scene_id",
                    "scene_digest",
                    "source_digest",
                    "source_snapshot_id",
                    "document_revision",
                )
                if result.get(key) is not None
            }
            child_ref["idempotency_key"] = action["idempotency_key"]
            await self.repository.complete_action(
                action["action_id"],
                self.worker_id,
                result,
                child_ref=child_ref,
            )
        except Exception as error:
            error_code = getattr(error, "code", type(error).__name__)
            # A port can explicitly prove an error happened before dispatch.
            # Every other error after the durable start boundary is unknown for
            # write effects and must go through recovery/reconciliation.
            if action["effect_class"] == "write" and dispatch_started:
                await self.repository.mark_action_outcome_unknown(
                    action["action_id"], self.worker_id, error_code
                )
            else:
                await self.repository.fail_action(
                    action["action_id"], self.worker_id, error_code
                )
        return True

    async def reconcile_restart(self) -> int:
        """Reconcile started child identities; writes are never redispatched here."""
        reclaimed = await self.repository.reclaim_expired_actions()
        for action in await self.repository.list_actions_for_reconcile():
            child_ref = action.get("child_ref")
            if child_ref is None:
                continue
            port = (
                self.scene_port
                if action["action_kind"] in _SCENE_ACTIONS
                else self.port
            )
            lookup = getattr(port, "reconcile", None)
            if lookup is None:
                continue
            try:
                outcome = await lookup(
                    action["action_kind"],
                    child_ref,
                    idempotency_key=action["idempotency_key"],
                )
            except Exception:
                # Reconnect can happen after process startup. Keep the durable
                # child identity and retry reconciliation; never redispatch a
                # started write through the normal claim path.
                continue
            if outcome is not None:
                await self.repository.record_reconciled_outcome(action["action_id"], outcome)
        return reclaimed
