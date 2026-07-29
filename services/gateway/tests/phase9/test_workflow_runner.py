import pytest

from autocad_gateway.workflows.runner import WorkflowRunner

from test_workflow_repository import _run, repo


class Port:
    def __init__(self, result=None, error=None, reconciled=None):
        self.result, self.error, self.reconciled = result, error, reconciled
        self.calls = []

    async def dispatch(self, action_kind, payload, *, idempotency_key):
        self.calls.append((action_kind, idempotency_key))
        if self.error:
            raise self.error
        return self.result or {"state": "succeeded"}

    async def reconcile(self, action_kind, child_ref, *, idempotency_key):
        return self.reconciled


@pytest.mark.asyncio
async def test_runner_completes_pure_and_write_actions(repo):
    await _run(repo)
    await repo.transition_run(
        owner_subject="alice",
        run_id="run",
        expected_state="created",
        expected_version=0,
        target="running",
    )
    for step, effect in (("plan", "read"), ("commit", "write")):
        await repo.create_step(
            owner_subject="alice",
            run_id="run",
            step_id=step,
            attempt=1,
            kind="run_planner" if effect == "read" else "request_commit",
        )
        await repo.insert_action(owner_subject="alice", run_id="run", step_id=step, attempt=1,
                                 action_kind="plan" if effect == "read" else "commit", payload={},
                                 retry_class="pure" if effect == "read" else "not_started", effect_class=effect)
        port = Port()
        assert await WorkflowRunner(repo, port, worker_id=step).run_once()
    actions = await repo.list_actions_for_reconcile()
    assert actions == []


@pytest.mark.asyncio
async def test_runner_read_error_fails_but_write_error_is_recovery(repo):
    await _run(repo)
    await repo.transition_run(
        owner_subject="alice",
        run_id="run",
        expected_state="created",
        expected_version=0,
        target="running",
    )
    await repo.create_step(
        owner_subject="alice",
        run_id="run",
        step_id="read",
        attempt=1,
        kind="query",
    )
    await repo.insert_action(owner_subject="alice", run_id="run", step_id="read", attempt=1, action_kind="query", payload={}, retry_class="read")
    assert await WorkflowRunner(repo, Port(error=RuntimeError("read")), worker_id="read").run_once()
    await repo.create_step(
        owner_subject="alice",
        run_id="run",
        step_id="write",
        attempt=1,
        kind="request_commit",
    )
    await repo.insert_action(owner_subject="alice", run_id="run", step_id="write", attempt=1, action_kind="commit", payload={}, retry_class="not_started", effect_class="write")
    assert await WorkflowRunner(repo, Port(error=RuntimeError("write")), worker_id="write").run_once()
    assert (await repo.get_run("alice", "run"))["state"] == "waiting_for_recovery"


@pytest.mark.asyncio
async def test_restart_reconciles_started_write_without_redispatch(repo):
    await _run(repo)
    await repo.transition_run(
        owner_subject="alice",
        run_id="run",
        expected_state="created",
        expected_version=0,
        target="running",
    )
    await repo.create_step(
        owner_subject="alice",
        run_id="run",
        step_id="write",
        attempt=1,
        kind="request_commit",
    )
    action, _ = await repo.insert_action(owner_subject="alice", run_id="run", step_id="write", attempt=1, action_kind="commit", payload={}, retry_class="not_started", effect_class="write")
    await repo.claim_action("old")
    await repo.mark_dispatch_started(action["action_id"], "old")
    port = Port(reconciled={"state": "succeeded", "result": {"job_id": "j"}})
    await WorkflowRunner(repo, port, worker_id="new").reconcile_restart()
    assert port.calls == []


@pytest.mark.asyncio
async def test_restart_reconciles_preview_child_without_redispatch(repo):
    await _run(repo)
    await repo.transition_run(
        owner_subject="alice",
        run_id="run",
        expected_state="created",
        expected_version=0,
        target="running",
    )
    await repo.create_step(
        owner_subject="alice",
        run_id="run",
        step_id="preview",
        attempt=1,
        kind="preview_program",
    )
    action, _ = await repo.insert_action(
        owner_subject="alice",
        run_id="run",
        step_id="preview",
        attempt=1,
        action_kind="preview",
        payload={"snapshot_id": "snapshot-a"},
        retry_class="read",
    )
    await repo.claim_action("old")
    await repo.mark_dispatch_started(action["action_id"], "old")
    port = Port(
        reconciled={
            "state": "succeeded",
            "result": {"preview_id": "preview-a"},
        }
    )
    await WorkflowRunner(repo, port, worker_id="new").reconcile_restart()
    reconciled = (await repo.list_actions("alice", "run"))[0]
    assert reconciled["state"] == "completed"
    assert port.calls == []
