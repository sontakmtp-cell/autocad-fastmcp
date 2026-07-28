"""Owner-scoped SQLite durable store for Phase 9 workflow runs.

The repository intentionally stores only safe, bounded JSON.  It never invokes
an Agent/Host; effects are represented by leased outbox actions.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from ...workflows.state import (
    TERMINAL_RUN_STATES, InvalidWorkflowTransition, child_idempotency_key,
    validate_run_transition, validate_step_transition,
)
from .database import SqliteDatabase, new_id, utc_now
from .repositories import RepositoryConflict

def _json(value: Any, limit: int = 262_144) -> str:
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise RepositoryConflict("workflow_payload_invalid") from error
    if len(result.encode()) > limit:
        raise RepositoryConflict("workflow_payload_too_large")
    return result

def _digest(value: Any) -> str:
    return "sha256:" + sha256(_json(value).encode()).hexdigest()

class Phase9Repository:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    async def create_run(self, *, owner_subject: str, actor_subject: str, run_id: str,
                         idempotency_key: str, pins: dict[str, Any], inputs: dict[str, Any],
                         device_id: str | None = None, initial_snapshot_id: str | None = None) -> tuple[dict[str, Any], bool]:
        if not owner_subject or not actor_subject or not run_id or not idempotency_key:
            raise RepositoryConflict("workflow_identity_invalid")
        encoded_pins, encoded_inputs = _json(pins), _json(inputs)
        now = utc_now()
        with self.database.transaction() as conn:
            existing = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND idempotency_key=?", (owner_subject, idempotency_key)).fetchone()
            if existing is not None:
                value = self._run(existing)
                if value["pins"] == pins and value["inputs"] == inputs and value["device_id"] == device_id:
                    return value, True
                raise RepositoryConflict("idempotency_conflict")
            try:
                conn.execute("""INSERT INTO workflow_runs(run_id,owner_subject,actor_subject,idempotency_key,pins_json,pins_digest,inputs_json,inputs_digest,device_id,initial_snapshot_id,state,state_version,current_step_id,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?, 'created',0,NULL,?,?)""",
                    (run_id,owner_subject,actor_subject,idempotency_key,encoded_pins,_digest(pins),encoded_inputs,_digest(inputs),device_id,initial_snapshot_id,now,now))
            except Exception as error:
                raise RepositoryConflict("workflow_run_create_failed") from error
            self._append_event(conn, run_id, "created", {"state": "created"})
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run(row), False

    async def get_run(self, owner_subject: str, run_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject, run_id)).fetchone()
        return self._run(row) if row else None

    async def transition_run(self, *, owner_subject: str, run_id: str, expected_state: str,
                             expected_version: int, target: str, current_step_id: str | None = None,
                             event_type: str = "state") -> dict[str, Any]:
        try: validate_run_transition(expected_state, target)
        except InvalidWorkflowTransition as error: raise RepositoryConflict("invalid_workflow_transition") from error
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject,run_id)).fetchone()
            if row is None: raise RepositoryConflict("not_found")
            if str(row["state"]) in TERMINAL_RUN_STATES: raise RepositoryConflict("terminal_immutable")
            if str(row["state"]) != expected_state or int(row["state_version"]) != expected_version: raise RepositoryConflict("stale_workflow_state")
            now=utc_now()
            update=conn.execute("UPDATE workflow_runs SET state=?,state_version=state_version+1,current_step_id=?,updated_at=? WHERE run_id=? AND state=? AND state_version=?", (target,current_step_id,now,run_id,expected_state,expected_version))
            if update.rowcount != 1: raise RepositoryConflict("stale_workflow_state")
            self._append_event(conn,run_id,event_type,{"from":expected_state,"to":target,"state_version":expected_version+1})
            row=conn.execute("SELECT * FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone()
        return self._run(row)

    async def insert_action(self, *, owner_subject: str, run_id: str, step_id: str, attempt: int,
                            action_kind: str, payload: dict[str, Any], retry_class: str,
                            effect_class: str = "read") -> tuple[dict[str, Any], bool]:
        if retry_class not in {"pure","read","metadata","not_started"} or effect_class not in {"read","write"}:
            raise RepositoryConflict("workflow_action_invalid")
        action_id = f"wfa:{run_id}:{step_id}:{attempt}:{action_kind}"
        key=child_idempotency_key(run_id,step_id,attempt,action_kind)
        payload_json=_json(payload); now=utc_now()
        with self.database.transaction() as conn:
            run=conn.execute("SELECT 1 FROM workflow_runs WHERE owner_subject=? AND run_id=?",(owner_subject,run_id)).fetchone()
            if run is None: raise RepositoryConflict("not_found")
            old=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
            if old:
                value=self._action(old)
                if value["payload"] == payload and value["retry_class"] == retry_class and value["effect_class"] == effect_class: return value,True
                raise RepositoryConflict("workflow_action_conflict")
            conn.execute("""INSERT INTO workflow_actions(action_id,run_id,step_id,attempt,action_kind,payload_json,payload_digest,idempotency_key,retry_class,effect_class,state,lease_owner,lease_expires_at,result_json,error_code,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?, 'pending',NULL,NULL,NULL,NULL,?,?)""",(action_id,run_id,step_id,attempt,action_kind,payload_json,_digest(payload),key,retry_class,effect_class,now,now))
            self._append_event(conn,run_id,"action_inserted",{"action_id":action_id,"step_id":step_id})
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
        return self._action(row),False

    async def claim_action(self, worker_id: str, *, lease_seconds: int = 30) -> dict[str, Any] | None:
        if not worker_id or lease_seconds < 1: raise RepositoryConflict("workflow_lease_invalid")
        with self.database.transaction() as conn:
            now=utc_now()
            row=conn.execute("SELECT * FROM workflow_actions WHERE state='pending' OR (state='claimed' AND lease_expires_at < datetime('now')) ORDER BY created_at,action_id LIMIT 1").fetchone()
            if row is None:return None
            # SQLite datetime strings are ISO UTC; modifier avoids app-held transaction/network wait.
            updated=conn.execute("UPDATE workflow_actions SET state='claimed',lease_owner=?,lease_expires_at=datetime('now', ?),updated_at=? WHERE action_id=? AND (state='pending' OR (state='claimed' AND lease_expires_at < datetime('now')))",(worker_id,f'+{lease_seconds} seconds',now,row['action_id']))
            if updated.rowcount != 1:return None
            fresh=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(row['action_id'],)).fetchone()
            self._append_event(conn,str(fresh['run_id']),"action_claimed",{"action_id":str(fresh['action_id'])})
        return self._action(fresh)

    async def complete_action(self, action_id: str, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return await self._finish_action(action_id,worker_id,"completed",result,None)
    async def fail_action(self, action_id: str, worker_id: str, error_code: str) -> dict[str, Any]:
        return await self._finish_action(action_id,worker_id,"failed",None,error_code)
    async def _finish_action(self, action_id: str, worker_id: str, state: str, result: dict[str, Any] | None, error_code: str | None) -> dict[str, Any]:
        with self.database.transaction() as conn:
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
            if row is None: raise RepositoryConflict("not_found")
            if str(row['state']) in {'completed','failed'}:
                return self._action(row)
            if str(row['state']) != 'claimed' or str(row['lease_owner']) != worker_id: raise RepositoryConflict("workflow_lease_lost")
            conn.execute("UPDATE workflow_actions SET state=?,result_json=?,error_code=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE action_id=?",(state,_json(result) if result is not None else None,error_code,utc_now(),action_id))
            self._append_event(conn,str(row['run_id']),"action_"+state,{"action_id":action_id,"error_code":error_code})
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
        return self._action(row)

    async def reclaim_expired_actions(self) -> int:
        with self.database.transaction() as conn:
            now=utc_now(); rows=conn.execute("SELECT action_id,run_id FROM workflow_actions WHERE state='claimed' AND lease_expires_at < datetime('now')").fetchall()
            conn.execute("UPDATE workflow_actions SET state='pending',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE state='claimed' AND lease_expires_at < datetime('now')",(now,))
            for row in rows:self._append_event(conn,str(row['run_id']),"action_reclaimed",{"action_id":str(row['action_id'])})
        return len(rows)

    async def create_wait(self, *, owner_subject: str, run_id: str, step_id: str, wait_kind: str, expected_state_version: int, response_schema: dict[str, Any], expires_at: str | None = None) -> dict[str, Any]:
        with self.database.transaction() as conn:
            run=conn.execute("SELECT state_version FROM workflow_runs WHERE owner_subject=? AND run_id=?",(owner_subject,run_id)).fetchone()
            if run is None: raise RepositoryConflict("not_found")
            if int(run['state_version']) != expected_state_version: raise RepositoryConflict("stale_workflow_state")
            wait_id=new_id('wfw'); schema_json=_json(response_schema,65_536)
            conn.execute("INSERT INTO workflow_waits(wait_id,run_id,step_id,wait_kind,expected_state_version,response_schema_json,response_schema_digest,expires_at,resolved_at,resolution_json,created_at) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?)",(wait_id,run_id,step_id,wait_kind,expected_state_version,schema_json,_digest(response_schema),expires_at,utc_now()))
            self._append_event(conn,run_id,"wait_created",{"wait_id":wait_id,"expected_state_version":expected_state_version})
            row=conn.execute("SELECT * FROM workflow_waits WHERE wait_id=?",(wait_id,)).fetchone()
        return self._wait(row)

    async def list_events(self, owner_subject: str, run_id: str, *, cursor: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        if await self.get_run(owner_subject,run_id) is None:return []
        with self.database.read_connection() as conn: rows=conn.execute("SELECT * FROM workflow_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",(run_id,cursor,limit)).fetchall()
        return [self._event(r) for r in rows]

    @staticmethod
    def _append_event(conn: Any, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        sequence=int(conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM workflow_events WHERE run_id=?",(run_id,)).fetchone()[0])
        conn.execute("INSERT INTO workflow_events(event_id,run_id,sequence,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",(new_id('wfe'),run_id,sequence,event_type,_json(payload,65_536),utc_now()))
    @staticmethod
    def _run(row: Any) -> dict[str, Any]:
        value=dict(row); value['pins']=json.loads(value.pop('pins_json')); value['inputs']=json.loads(value.pop('inputs_json')); value['state_version']=int(value['state_version']); return value
    @staticmethod
    def _action(row: Any) -> dict[str, Any]:
        value=dict(row); value['payload']=json.loads(value.pop('payload_json')); value['result']=json.loads(value.pop('result_json')) if value.get('result_json') else None; return value
    @staticmethod
    def _wait(row: Any) -> dict[str, Any]:
        value=dict(row); value['response_schema']=json.loads(value.pop('response_schema_json')); return value
    @staticmethod
    def _event(row: Any) -> dict[str, Any]:
        value=dict(row); value['sequence']=int(value['sequence']); value['payload']=json.loads(value.pop('payload_json')); return value
