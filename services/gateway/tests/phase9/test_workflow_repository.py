import pytest

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase9_repository import Phase9Repository
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict


PINS = {
    "skill_id": "test.workflow",
    "skill_version": "1.0.0",
    "skill_digest": "sha256:" + "1" * 64,
    "workflow_id": "test.workflow",
    "workflow_version": "1.0.0",
    "workflow_digest": "sha256:" + "2" * 64,
    "catalog_epoch": 1,
    "policy_epoch": 1,
    "planner_registry_version": "phase9-test/1",
    "planner_registry_hash": "sha256:" + "3" * 64,
}


@pytest.fixture
async def repo(tmp_path):
    database = SqliteDatabase(tmp_path / "workflow.sqlite")
    await database.open()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO workflow_definitions(
                workflow_id, version, definition_json, definition_digest,
                step_count, planner_refs_json, template_refs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PINS["workflow_id"],
                PINS["workflow_version"],
                "{}",
                PINS["workflow_digest"],
                1,
                "[]",
                "[]",
                "2026-07-28T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO skill_versions(
                skill_id, version, status, manifest_json, manifest_digest,
                workflow_id, workflow_version, workflow_digest, guide_digest,
                catalog_release_digest, published_at, created_at
            ) VALUES (?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PINS["skill_id"],
                PINS["skill_version"],
                "{}",
                PINS["skill_digest"],
                PINS["workflow_id"],
                PINS["workflow_version"],
                PINS["workflow_digest"],
                "sha256:" + "4" * 64,
                "sha256:" + "5" * 64,
                "2026-07-28T00:00:00+00:00",
                "2026-07-28T00:00:00+00:00",
            ),
        )
    yield Phase9Repository(database)
    await database.close()


async def _run(repo, owner="alice"):
    return (
        await repo.create_run(
            owner_subject=owner,
            actor_issuer="https://issuer.example/",
            actor_subject=owner,
            run_id="run",
            idempotency_key="start",
            pins=PINS,
            inputs={},
            device_id="device-1",
            device_identity_generation=1,
        )
    )[0]


@pytest.mark.asyncio
async def test_owner_cas_terminal_and_event_ordering(repo):
    await _run(repo)
    with pytest.raises(RepositoryConflict, match="not_found"):
        await repo.transition_run(owner_subject="bob", run_id="run", expected_state="created", expected_version=0, target="running")
    running = await repo.transition_run(owner_subject="alice", run_id="run", expected_state="created", expected_version=0, target="running")
    with pytest.raises(RepositoryConflict, match="stale_workflow_state"):
        await repo.transition_run(owner_subject="alice", run_id="run", expected_state="running", expected_version=0, target="paused")
    done = await repo.transition_run(owner_subject="alice", run_id="run", expected_state="running", expected_version=1, target="succeeded")
    assert done["state_version"] == 2
    with pytest.raises(RepositoryConflict, match="terminal_immutable"):
        await repo.transition_run(owner_subject="alice", run_id="run", expected_state="succeeded", expected_version=2, target="running")
    assert [event["sequence"] for event in await repo.list_events("alice", "run")] == [1, 2, 3]


@pytest.mark.asyncio
async def test_duplicate_start_wait_step_and_action_are_idempotent(repo):
    run = await _run(repo)
    duplicate, replayed = await repo.create_run(
        owner_subject="alice",
        actor_issuer="https://issuer.example/",
        actor_subject="alice",
        run_id="other",
        idempotency_key="start",
        pins=PINS,
        inputs={},
        device_id="device-1",
        device_identity_generation=1,
    )
    assert replayed and duplicate["run_id"] == run["run_id"]
    step, replayed = await repo.create_step(owner_subject="alice", run_id="run", step_id="s", attempt=1, kind="plan")
    assert not replayed
    assert (await repo.create_step(owner_subject="alice", run_id="run", step_id="s", attempt=1, kind="plan"))[1]
    action, replayed = await repo.insert_action(owner_subject="alice", run_id="run", step_id="s", attempt=1, action_kind="plan", payload={}, retry_class="pure")
    assert not replayed and action["idempotency_key"] == "wf:run:s:1:plan"
    assert (await repo.insert_action(owner_subject="alice", run_id="run", step_id="s", attempt=1, action_kind="plan", payload={}, retry_class="pure"))[1]


@pytest.mark.asyncio
async def test_scene_action_key_and_payload_survive_restart_and_replay(repo):
    await _run(repo)
    await repo.create_step(
        owner_subject="alice",
        run_id="run",
        step_id="scene",
        attempt=1,
        kind="build_scene",
    )
    source_digest = "sha256:" + "a" * 64
    payload = {
        "source_snapshot_id": "snapshot-a",
        "source_digest": source_digest,
    }
    created, replayed = await repo.insert_action(
        owner_subject="alice",
        run_id="run",
        step_id="scene",
        attempt=1,
        action_kind="build_scene",
        payload=payload,
        retry_class="read",
        source_digest=source_digest,
    )
    assert replayed is False
    assert created["idempotency_key"].endswith(source_digest)
    await repo.database.close()
    await repo.database.open()
    replay, replayed = await repo.insert_action(
        owner_subject="alice",
        run_id="run",
        step_id="scene",
        attempt=1,
        action_kind="build_scene",
        payload=payload,
        retry_class="read",
        source_digest=source_digest,
    )
    assert replayed is True
    assert replay["action_id"] == created["action_id"]
    with pytest.raises(RepositoryConflict, match="workflow_action_conflict"):
        await repo.insert_action(
            owner_subject="alice",
            run_id="run",
            step_id="scene",
            attempt=1,
            action_kind="build_scene",
            payload={**payload, "source_snapshot_id": "snapshot-b"},
            retry_class="read",
            source_digest=source_digest,
        )


@pytest.mark.asyncio
async def test_started_write_is_never_reclaimed_and_unknown_enters_recovery(repo):
    await _run(repo)
    await repo.transition_run(owner_subject="alice", run_id="run", expected_state="created", expected_version=0, target="running")
    await repo.create_step(
        owner_subject="alice",
        run_id="run",
        step_id="commit",
        attempt=1,
        kind="request_commit",
    )
    action, _ = await repo.insert_action(owner_subject="alice", run_id="run", step_id="commit", attempt=1, action_kind="commit", payload={}, retry_class="not_started", effect_class="write")
    claimed = await repo.claim_action("one", lease_seconds=1)
    started = await repo.mark_dispatch_started(action["action_id"], "one")
    assert started["state"] == "started"
    with repo.database.transaction() as connection:
        connection.execute("UPDATE workflow_actions SET lease_expires_at=datetime('now','-1 second') WHERE action_id=?", (action["action_id"],))
    assert await repo.reclaim_expired_actions() == 0
    await repo.mark_action_outcome_unknown(action["action_id"], "one", "crash")
    assert (await repo.get_run("alice", "run"))["state"] == "waiting_for_recovery"
    assert await repo.claim_action("two") is None


@pytest.mark.asyncio
async def test_wait_exact_state_schema_and_idempotency(repo):
    await _run(repo)
    await repo.transition_run(owner_subject="alice", run_id="run", expected_state="created", expected_version=0, target="running")
    await repo.transition_run(owner_subject="alice", run_id="run", expected_state="running", expected_version=1, target="waiting_for_user")
    wait = await repo.create_wait(owner_subject="alice", run_id="run", step_id="input", wait_kind="user_input", expected_state_version=2, response_schema={"type": "object"})
    value, duplicate = await repo.resolve_wait(owner_subject="alice", run_id="run", wait_id=wait["wait_id"], expected_state_version=2, response_schema_digest=wait["response_schema_digest"], response={"x": 1}, idempotency_key="control")
    assert not duplicate
    assert (await repo.resolve_wait(owner_subject="alice", run_id="run", wait_id=wait["wait_id"], expected_state_version=2, response_schema_digest=wait["response_schema_digest"], response={"x": 1}, idempotency_key="control"))[1]
    with pytest.raises(RepositoryConflict, match="idempotency_conflict"):
        await repo.resolve_wait(owner_subject="alice", run_id="run", wait_id=wait["wait_id"], expected_state_version=2, response_schema_digest=wait["response_schema_digest"], response={"x": 2}, idempotency_key="control")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger",
    [
        """
        CREATE TRIGGER fail_after_run BEFORE INSERT ON workflow_steps
        BEGIN SELECT RAISE(ABORT, 'crash_after_run'); END
        """,
        """
        CREATE TRIGGER fail_during_steps BEFORE INSERT ON workflow_steps
        WHEN NEW.step_id='b'
        BEGIN SELECT RAISE(ABORT, 'crash_during_steps'); END
        """,
        """
        CREATE TRIGGER fail_before_running BEFORE UPDATE ON workflow_runs
        WHEN NEW.state='running'
        BEGIN SELECT RAISE(ABORT, 'crash_before_running'); END
        """,
    ],
)
async def test_initialized_run_rolls_back_on_failure_injection(repo, trigger):
    with repo.database.transaction() as connection:
        connection.execute(trigger)
    with pytest.raises(Exception):
        await repo.create_run(
            owner_subject="alice",
            actor_issuer="https://issuer.example/",
            actor_subject="alice",
            run_id="atomic-run",
            idempotency_key="atomic-start",
            pins=PINS,
            inputs={},
            device_id="device-1",
            device_identity_generation=1,
            steps=[
                {"step_id": "a", "kind": "query", "input_ref": {}},
                {"step_id": "b", "kind": "report", "input_ref": {}},
            ],
            first_step_id="a",
        )
    assert await repo.get_run("alice", "atomic-run") is None


@pytest.mark.asyncio
async def test_cancel_keeps_started_write_but_cancels_not_started_actions(repo):
    await _run(repo)
    await repo.transition_run(
        owner_subject="alice",
        run_id="run",
        expected_state="created",
        expected_version=0,
        target="running",
    )
    for step, action_kind in (("started", "commit"), ("pending", "rollback")):
        await repo.create_step(
            owner_subject="alice",
            run_id="run",
            step_id=step,
            attempt=1,
            kind="request_commit",
        )
        await repo.insert_action(
            owner_subject="alice",
            run_id="run",
            step_id=step,
            attempt=1,
            action_kind=action_kind,
            payload={},
            retry_class="not_started",
            effect_class="write",
        )
    claimed = await repo.claim_action("worker")
    await repo.mark_dispatch_started(claimed["action_id"], "worker")
    result = await repo.cancel_run(
        owner_subject="alice",
        run_id="run",
        expected_state="running",
        expected_version=1,
    )
    assert result["state"] == "waiting_for_recovery"
    states = {
        action["action_kind"]: action["state"]
        for action in await repo.list_actions("alice", "run")
    }
    assert states == {"commit": "started", "rollback": "cancelled"}
    assert await repo.claim_action("other") is None


@pytest.mark.asyncio
async def test_conflicting_duplicate_action_completion_is_rejected(repo):
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
        step_id="action",
        attempt=1,
        kind="query",
    )
    action, _ = await repo.insert_action(
        owner_subject="alice",
        run_id="run",
        step_id="action",
        attempt=1,
        action_kind="query",
        payload={},
        retry_class="read",
    )
    await repo.claim_action("worker")
    expected = {"value": 1}
    await repo.complete_action(
        action["action_id"],
        "worker",
        expected,
        child_ref={"idempotency_key": action["idempotency_key"]},
    )
    duplicate = await repo.complete_action(
        action["action_id"],
        "worker",
        expected,
        child_ref={"idempotency_key": action["idempotency_key"]},
    )
    assert duplicate["result"] == expected
    with pytest.raises(RepositoryConflict, match="workflow_action_conflict"):
        await repo.complete_action(
            action["action_id"],
            "worker",
            {"value": 2},
            child_ref={"idempotency_key": action["idempotency_key"]},
        )
