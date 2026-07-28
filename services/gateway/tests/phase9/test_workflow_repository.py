import pytest

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.infrastructure.sqlite.phase9_repository import Phase9Repository
from autocad_gateway.infrastructure.sqlite.repositories import RepositoryConflict


SCHEMA = """
CREATE TABLE workflow_runs(run_id TEXT PRIMARY KEY,owner_subject TEXT,actor_subject TEXT,idempotency_key TEXT,pins_json TEXT,pins_digest TEXT,inputs_json TEXT,inputs_digest TEXT,device_id TEXT,initial_snapshot_id TEXT,state TEXT,state_version INTEGER,current_step_id TEXT,created_at TEXT,updated_at TEXT,UNIQUE(owner_subject,idempotency_key));
CREATE TABLE workflow_steps(run_id TEXT,step_id TEXT,attempt INTEGER,kind TEXT,state TEXT,state_version INTEGER,input_ref_json TEXT,output_ref_json TEXT,error_code TEXT,created_at TEXT,updated_at TEXT,PRIMARY KEY(run_id,step_id,attempt));
CREATE TABLE workflow_actions(action_id TEXT PRIMARY KEY,run_id TEXT,step_id TEXT,attempt INTEGER,action_kind TEXT,payload_json TEXT,payload_digest TEXT,idempotency_key TEXT,retry_class TEXT,effect_class TEXT,state TEXT,lease_owner TEXT,lease_expires_at TEXT,dispatch_started_at TEXT,child_state TEXT,child_ref_json TEXT,result_json TEXT,error_code TEXT,created_at TEXT,updated_at TEXT);
CREATE TABLE workflow_waits(wait_id TEXT PRIMARY KEY,run_id TEXT,step_id TEXT,wait_kind TEXT,expected_state_version INTEGER,response_schema_json TEXT,response_schema_digest TEXT,expires_at TEXT,resolved_at TEXT,resolution_json TEXT,created_at TEXT);
CREATE TABLE workflow_events(event_id TEXT PRIMARY KEY,run_id TEXT,sequence INTEGER,event_type TEXT,payload_json TEXT,created_at TEXT,UNIQUE(run_id,sequence));
"""


@pytest.fixture
async def repo(tmp_path):
    database = SqliteDatabase(tmp_path / "workflow.sqlite")
    await database.open()
    with database.transaction() as connection:
        connection.executescript(SCHEMA)
    yield Phase9Repository(database)
    await database.close()


async def _run(repo, owner="alice"):
    return (await repo.create_run(owner_subject=owner, actor_subject=owner, run_id="run", idempotency_key="start", pins={"skill": "v1"}, inputs={}))[0]


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
    duplicate, replayed = await repo.create_run(owner_subject="alice", actor_subject="alice", run_id="other", idempotency_key="start", pins={"skill": "v1"}, inputs={})
    assert replayed and duplicate["run_id"] == run["run_id"]
    step, replayed = await repo.create_step(owner_subject="alice", run_id="run", step_id="s", attempt=1, kind="plan")
    assert not replayed
    assert (await repo.create_step(owner_subject="alice", run_id="run", step_id="s", attempt=1, kind="plan"))[1]
    action, replayed = await repo.insert_action(owner_subject="alice", run_id="run", step_id="s", attempt=1, action_kind="plan", payload={}, retry_class="pure")
    assert not replayed and action["idempotency_key"] == "wf:run:s:1:plan"
    assert (await repo.insert_action(owner_subject="alice", run_id="run", step_id="s", attempt=1, action_kind="plan", payload={}, retry_class="pure"))[1]


@pytest.mark.asyncio
async def test_started_write_is_never_reclaimed_and_unknown_enters_recovery(repo):
    await _run(repo)
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
