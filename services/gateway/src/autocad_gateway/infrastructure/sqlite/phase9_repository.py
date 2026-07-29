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

    async def create_run(
        self,
        *,
        owner_subject: str,
        actor_issuer: str,
        actor_subject: str,
        run_id: str,
        idempotency_key: str,
        pins: dict[str, Any],
        inputs: dict[str, Any],
        device_id: str,
        device_identity_generation: int,
        initial_snapshot_id: str | None = None,
        initial_document_id: str | None = None,
        initial_document_revision: str | None = None,
        expires_at: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        first_step_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if (
            not owner_subject
            or not actor_issuer
            or not actor_subject
            or not run_id
            or not idempotency_key
            or not device_id
            or device_identity_generation < 1
        ):
            raise RepositoryConflict("workflow_identity_invalid")
        required_pins = {
            "skill_id",
            "skill_version",
            "skill_digest",
            "workflow_id",
            "workflow_version",
            "workflow_digest",
            "catalog_epoch",
            "policy_epoch",
            "planner_registry_version",
            "planner_registry_hash",
        }
        if not required_pins <= set(pins):
            raise RepositoryConflict("workflow_pins_invalid")
        if steps is not None and (
            not steps
            or len(steps) > 64
            or not first_step_id
            or len({str(step.get("step_id")) for step in steps}) != len(steps)
        ):
            raise RepositoryConflict("workflow_steps_invalid")
        encoded_pins = _json(pins)
        encoded_inputs = _json(inputs)
        request = {
            "actor_issuer": actor_issuer,
            "actor_subject": actor_subject,
            "pins": pins,
            "inputs": inputs,
            "device_id": device_id,
            "device_identity_generation": device_identity_generation,
            "initial_snapshot_id": initial_snapshot_id,
            "initial_document_id": initial_document_id,
            "initial_document_revision": initial_document_revision,
        }
        request_hash = _digest(request)
        now = utc_now()
        with self.database.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM workflow_runs "
                "WHERE owner_subject=? AND idempotency_key=?",
                (owner_subject, idempotency_key),
            ).fetchone()
            if existing is not None:
                value = self._run(existing)
                if value["request_hash"] == request_hash:
                    if steps is not None:
                        rows = conn.execute(
                            "SELECT * FROM workflow_steps WHERE run_id=? "
                            "ORDER BY step_id,attempt",
                            (value["run_id"],),
                        ).fetchall()
                        actual = [
                            (
                                str(row["step_id"]),
                                int(row["attempt"]),
                                str(row["kind"]),
                                json.loads(row["input_ref_json"])
                                if row["input_ref_json"]
                                else None,
                            )
                            for row in rows
                        ]
                        expected = sorted(
                            (
                                str(step["step_id"]),
                                int(step.get("attempt", 1)),
                                str(step["kind"]),
                                step.get("input_ref"),
                            )
                            for step in steps
                        )
                        if actual != expected or value["state"] == "created":
                            raise RepositoryConflict(
                                "workflow_initialization_incomplete"
                            )
                    return value, True
                raise RepositoryConflict("idempotency_conflict")
            try:
                conn.execute(
                    """
                    INSERT INTO workflow_runs(
                        run_id, owner_subject, actor_issuer, actor_subject,
                        skill_id, skill_version, skill_digest,
                        workflow_id, workflow_version, workflow_digest,
                        catalog_epoch, policy_epoch,
                        planner_registry_version, planner_registry_hash,
                        pins_json, pins_digest, inputs_json, inputs_digest,
                        device_id, device_identity_generation,
                        initial_snapshot_id, initial_document_id,
                        initial_document_revision,
                        state, state_version, current_step_id,
                        idempotency_key, request_hash,
                        created_at, updated_at, expires_at
                    )
                    VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        'created', 0, NULL,
                        ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        run_id,
                        owner_subject,
                        actor_issuer,
                        actor_subject,
                        pins["skill_id"],
                        pins["skill_version"],
                        pins["skill_digest"],
                        pins["workflow_id"],
                        pins["workflow_version"],
                        pins["workflow_digest"],
                        pins["catalog_epoch"],
                        pins["policy_epoch"],
                        pins["planner_registry_version"],
                        pins["planner_registry_hash"],
                        encoded_pins,
                        _digest(pins),
                        encoded_inputs,
                        _digest(inputs),
                        device_id,
                        device_identity_generation,
                        initial_snapshot_id,
                        initial_document_id,
                        initial_document_revision,
                        idempotency_key,
                        request_hash,
                        now,
                        now,
                        expires_at,
                    ),
                )
            except Exception as error:
                raise RepositoryConflict("workflow_run_create_failed") from error
            self._append_event(conn, run_id, "created", {"state": "created"})
            if steps is not None:
                for step in steps:
                    step_now = utc_now()
                    conn.execute(
                        """
                        INSERT INTO workflow_steps(
                            run_id,step_id,attempt,kind,state,state_version,
                            input_ref_json,output_ref_json,error_code,created_at,updated_at
                        ) VALUES(?,?,?,?, 'pending',0,?,NULL,NULL,?,?)
                        """,
                        (
                            run_id,
                            str(step["step_id"]),
                            int(step.get("attempt", 1)),
                            str(step["kind"]),
                            _json(step.get("input_ref"))
                            if step.get("input_ref") is not None
                            else None,
                            step_now,
                            step_now,
                        ),
                    )
                    self._append_event(
                        conn,
                        run_id,
                        "step_created",
                        {
                            "step_id": str(step["step_id"]),
                            "attempt": int(step.get("attempt", 1)),
                        },
                    )
                changed = conn.execute(
                    "UPDATE workflow_steps SET state='ready',state_version=1,"
                    "updated_at=? WHERE run_id=? AND step_id=? AND attempt=1 "
                    "AND state='pending' AND state_version=0",
                    (utc_now(), run_id, first_step_id),
                )
                if changed.rowcount != 1:
                    raise RepositoryConflict("workflow_first_step_invalid")
                self._append_event(
                    conn,
                    run_id,
                    "step_state",
                    {
                        "step_id": first_step_id,
                        "from": "pending",
                        "to": "ready",
                    },
                )
                conn.execute(
                    "UPDATE workflow_runs SET state='running',state_version=1,"
                    "current_step_id=?,updated_at=? WHERE run_id=?",
                    (first_step_id, utc_now(), run_id),
                )
                self._append_event(
                    conn,
                    run_id,
                    "started",
                    {"from": "created", "to": "running", "state_version": 1},
                )
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run(row), False

    async def get_run(self, owner_subject: str, run_id: str) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject, run_id)).fetchone()
        return self._run(row) if row else None

    async def list_runs(
        self, owner_subject: str, *, cursor: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        if cursor < 0 or not 1 <= limit <= 100:
            raise RepositoryConflict("invalid_request")
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE owner_subject=? "
                "ORDER BY created_at DESC,run_id DESC LIMIT ? OFFSET ?",
                (owner_subject, limit, cursor),
            ).fetchall()
        return [self._run(row) for row in rows]

    async def list_steps(
        self, owner_subject: str, run_id: str
    ) -> list[dict[str, Any]]:
        if await self.get_run(owner_subject, run_id) is None:
            raise RepositoryConflict("not_found")
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? "
                "ORDER BY created_at,step_id,attempt",
                (run_id,),
            ).fetchall()
        return [self._step(row) for row in rows]

    async def list_actions(
        self, owner_subject: str, run_id: str
    ) -> list[dict[str, Any]]:
        if await self.get_run(owner_subject, run_id) is None:
            raise RepositoryConflict("not_found")
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_actions WHERE run_id=? "
                "ORDER BY created_at,action_id",
                (run_id,),
            ).fetchall()
        return [self._action(row) for row in rows]

    async def current_wait(
        self, owner_subject: str, run_id: str
    ) -> dict[str, Any] | None:
        if await self.get_run(owner_subject, run_id) is None:
            raise RepositoryConflict("not_found")
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_waits WHERE run_id=? AND resolved_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return self._wait(row) if row is not None else None

    async def begin_control_command(
        self,
        *,
        owner_subject: str,
        run_id: str,
        action: str,
        expected_state_version: int,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        encoded = _json(payload, 65_536)
        digest = _digest(payload)
        with self.database.transaction() as conn:
            run = conn.execute(
                "SELECT state_version FROM workflow_runs "
                "WHERE owner_subject=? AND run_id=?",
                (owner_subject, run_id),
            ).fetchone()
            if run is None:
                raise RepositoryConflict("not_found")
            old = conn.execute(
                "SELECT * FROM workflow_control_commands "
                "WHERE owner_subject=? AND idempotency_key=?",
                (owner_subject, idempotency_key),
            ).fetchone()
            if old is not None:
                value = self._command(old)
                if (
                    value["run_id"] == run_id
                    and value["action"] == action
                    and value["expected_state_version"] == expected_state_version
                    and value["payload_digest"] == digest
                ):
                    return value, True
                raise RepositoryConflict("idempotency_conflict")
            if int(run["state_version"]) != expected_state_version:
                raise RepositoryConflict("stale_workflow_state")
            conn.execute(
                """
                INSERT INTO workflow_control_commands(
                    owner_subject,run_id,idempotency_key,action,
                    expected_state_version,payload_json,payload_digest,
                    state,result_json,created_at,completed_at
                ) VALUES(?,?,?,?,?,?,?,'started',NULL,?,NULL)
                """,
                (
                    owner_subject,
                    run_id,
                    idempotency_key,
                    action,
                    expected_state_version,
                    encoded,
                    digest,
                    utc_now(),
                ),
            )
            self._append_event(
                conn,
                run_id,
                "control_started",
                {"action": action, "idempotency_key": idempotency_key},
            )
            row = conn.execute(
                "SELECT * FROM workflow_control_commands "
                "WHERE owner_subject=? AND idempotency_key=?",
                (owner_subject, idempotency_key),
            ).fetchone()
        return self._command(row), False

    async def complete_control_command(
        self,
        *,
        owner_subject: str,
        idempotency_key: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        encoded = _json(result, 65_536)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_control_commands "
                "WHERE owner_subject=? AND idempotency_key=?",
                (owner_subject, idempotency_key),
            ).fetchone()
            if row is None:
                raise RepositoryConflict("not_found")
            current = self._command(row)
            if current["state"] == "completed":
                if current["result"] == result:
                    return current
                raise RepositoryConflict("idempotency_conflict")
            now = utc_now()
            conn.execute(
                "UPDATE workflow_control_commands SET state='completed',"
                "result_json=?,completed_at=? "
                "WHERE owner_subject=? AND idempotency_key=? AND state='started'",
                (encoded, now, owner_subject, idempotency_key),
            )
            self._append_event(
                conn,
                str(row["run_id"]),
                "control_completed",
                {
                    "action": str(row["action"]),
                    "idempotency_key": idempotency_key,
                },
            )
            row = conn.execute(
                "SELECT * FROM workflow_control_commands "
                "WHERE owner_subject=? AND idempotency_key=?",
                (owner_subject, idempotency_key),
            ).fetchone()
        return self._command(row)

    async def wait_resolved_by_command(
        self, owner_subject: str, run_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        if await self.get_run(owner_subject, run_id) is None:
            raise RepositoryConflict("not_found")
        needle = f'%"idempotency_key":"{idempotency_key}"%'
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_waits WHERE run_id=? "
                "AND resolution_json LIKE ? ORDER BY created_at DESC LIMIT 1",
                (run_id, needle),
            ).fetchone()
        return self._wait(row) if row is not None else None

    async def resolve_open_waits_system(
        self, *, owner_subject: str, run_id: str, reason: str
    ) -> int:
        with self.database.transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM workflow_runs WHERE owner_subject=? AND run_id=?",
                (owner_subject, run_id),
            ).fetchone() is None:
                raise RepositoryConflict("not_found")
            rows = conn.execute(
                "SELECT wait_id FROM workflow_waits "
                "WHERE run_id=? AND resolved_at IS NULL",
                (run_id,),
            ).fetchall()
            now = utc_now()
            for row in rows:
                conn.execute(
                    "UPDATE workflow_waits SET resolved_at=?,resolution_json=? "
                    "WHERE wait_id=? AND resolved_at IS NULL",
                    (
                        now,
                        _json({"system": True, "reason": reason}),
                        row["wait_id"],
                    ),
                )
                self._append_event(
                    conn,
                    run_id,
                    "wait_resolved",
                    {"wait_id": str(row["wait_id"]), "reason": reason},
                )
        return len(rows)

    async def transition_run(self, *, owner_subject: str, run_id: str, expected_state: str,
                             expected_version: int, target: str, current_step_id: str | None = None,
                             event_type: str = "state") -> dict[str, Any]:
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject,run_id)).fetchone()
            if row is None: raise RepositoryConflict("not_found")
            if str(row["state"]) in TERMINAL_RUN_STATES: raise RepositoryConflict("terminal_immutable")
            if str(row["state"]) != expected_state or int(row["state_version"]) != expected_version: raise RepositoryConflict("stale_workflow_state")
            try: validate_run_transition(expected_state, target)
            except InvalidWorkflowTransition as error: raise RepositoryConflict("invalid_workflow_transition") from error
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
            conn.execute("""INSERT INTO workflow_actions(action_id,run_id,step_id,attempt,action_kind,payload_json,payload_digest,idempotency_key,retry_class,effect_class,state,lease_owner,lease_expires_at,dispatch_started_at,child_state,child_ref_json,result_json,error_code,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?, 'pending',NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?)""",(action_id,run_id,step_id,attempt,action_kind,payload_json,_digest(payload),key,retry_class,effect_class,now,now))
            self._append_event(conn,run_id,"action_inserted",{"action_id":action_id,"step_id":step_id})
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
        return self._action(row),False

    async def claim_action(self, worker_id: str, *, lease_seconds: int = 30) -> dict[str, Any] | None:
        if not worker_id or lease_seconds < 1: raise RepositoryConflict("workflow_lease_invalid")
        with self.database.transaction() as conn:
            now=utc_now()
            row=conn.execute("""SELECT a.* FROM workflow_actions AS a
                JOIN workflow_runs AS r ON r.run_id=a.run_id
                WHERE r.state='running' AND (
                    a.state='pending'
                    OR (a.state='claimed' AND a.lease_expires_at < datetime('now'))
                    OR (a.state='started' AND a.effect_class='read'
                        AND a.lease_expires_at < datetime('now'))
                )
                ORDER BY a.created_at,a.action_id LIMIT 1""").fetchone()
            if row is None:return None
            # SQLite datetime strings are ISO UTC; modifier avoids app-held transaction/network wait.
            updated=conn.execute("""UPDATE workflow_actions SET state='claimed',lease_owner=?,lease_expires_at=datetime('now', ?),updated_at=?
                WHERE action_id=? AND (state='pending' OR (state='claimed' AND lease_expires_at < datetime('now')) OR (state='started' AND effect_class='read' AND lease_expires_at < datetime('now')))""",(worker_id,f'+{lease_seconds} seconds',now,row['action_id']))
            if updated.rowcount != 1:return None
            fresh=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(row['action_id'],)).fetchone()
            self._append_event(conn,str(fresh['run_id']),"action_claimed",{"action_id":str(fresh['action_id'])})
        return self._action(fresh)

    async def mark_dispatch_started(self, action_id: str, worker_id: str) -> dict[str, Any]:
        """Record the exact child identity before calling a child service."""
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM workflow_actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise RepositoryConflict("not_found")
            if str(row["state"]) == "started":
                if str(row["lease_owner"]) != worker_id:
                    raise RepositoryConflict("workflow_lease_lost")
                return self._action(row)
            if str(row["state"]) != "claimed" or str(row["lease_owner"]) != worker_id:
                raise RepositoryConflict("workflow_lease_lost")
            now = utc_now()
            conn.execute("""UPDATE workflow_actions SET state='started',dispatch_started_at=?,child_state='started',child_ref_json=?,updated_at=?
                WHERE action_id=? AND state='claimed' AND lease_owner=?""",
                (
                    now,
                    _json(
                        {
                            "idempotency_key": str(row["idempotency_key"]),
                            "payload": json.loads(row["payload_json"]),
                        }
                    ),
                    now,
                    action_id,
                    worker_id,
                ))
            self._append_event(conn, str(row["run_id"]), "action_dispatch_started", {"action_id": action_id})
            row = conn.execute("SELECT * FROM workflow_actions WHERE action_id=?", (action_id,)).fetchone()
        return self._action(row)

    async def complete_action(
        self,
        action_id: str,
        worker_id: str,
        result: dict[str, Any],
        *,
        child_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._finish_action(
            action_id, worker_id, "completed", result, None, child_ref
        )
    async def fail_action(self, action_id: str, worker_id: str, error_code: str) -> dict[str, Any]:
        return await self._finish_action(
            action_id, worker_id, "failed", None, error_code, None
        )
    async def _finish_action(
        self,
        action_id: str,
        worker_id: str,
        state: str,
        result: dict[str, Any] | None,
        error_code: str | None,
        child_ref: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with self.database.transaction() as conn:
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
            if row is None: raise RepositoryConflict("not_found")
            if str(row['state']) in {'completed','failed'}:
                existing = self._action(row)
                if (
                    str(row["state"]) == state
                    and existing.get("result") == result
                    and row["error_code"] == error_code
                    and (
                        child_ref is None
                        or existing.get("child_ref") == child_ref
                    )
                ):
                    return existing
                raise RepositoryConflict("workflow_action_conflict")
            if str(row['state']) not in {'claimed', 'started'} or str(row['lease_owner']) != worker_id: raise RepositoryConflict("workflow_lease_lost")
            conn.execute(
                "UPDATE workflow_actions SET state=?,result_json=?,error_code=?,"
                "child_state=?,child_ref_json=COALESCE(?,child_ref_json),"
                "lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE action_id=?",
                (
                    state,
                    _json(result) if result is not None else None,
                    error_code,
                    "succeeded" if state == "completed" else "failed",
                    _json(child_ref) if child_ref is not None else None,
                    utc_now(),
                    action_id,
                ),
            )
            self._append_event(conn,str(row['run_id']),"action_"+state,{"action_id":action_id,"error_code":error_code})
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
        return self._action(row)

    async def mark_action_outcome_unknown(self, action_id: str, worker_id: str, error_code: str) -> dict[str, Any]:
        """Fail closed after a started write; recovery, never retry, decides it."""
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM workflow_actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise RepositoryConflict("not_found")
            if str(row["state"]) in {"completed", "failed", "outcome_unknown"}:
                return self._action(row)
            if str(row["state"]) != "started" or str(row["lease_owner"]) != worker_id:
                raise RepositoryConflict("workflow_lease_lost")
            now = utc_now()
            conn.execute("UPDATE workflow_actions SET state='outcome_unknown',child_state='outcome_unknown',error_code=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE action_id=?", (error_code, now, action_id))
            run = conn.execute("SELECT state,state_version FROM workflow_runs WHERE run_id=?", (row["run_id"],)).fetchone()
            if run is not None and str(run["state"]) not in TERMINAL_RUN_STATES:
                try:
                    validate_run_transition(str(run["state"]), "waiting_for_recovery")
                except InvalidWorkflowTransition as error:
                    raise RepositoryConflict("recovery_transition_invalid") from error
                conn.execute("UPDATE workflow_runs SET state='waiting_for_recovery',state_version=state_version+1,updated_at=? WHERE run_id=?", (now, row["run_id"]))
                self._append_event(conn, str(row["run_id"]), "waiting_for_recovery", {"action_id": action_id})
            self._append_event(conn, str(row["run_id"]), "action_outcome_unknown", {"action_id": action_id, "error_code": error_code})
            row = conn.execute("SELECT * FROM workflow_actions WHERE action_id=?", (action_id,)).fetchone()
        return self._action(row)

    async def reclaim_expired_actions(self) -> int:
        with self.database.transaction() as conn:
            now=utc_now(); rows=conn.execute("SELECT action_id,run_id FROM workflow_actions WHERE (state='claimed' OR (state='started' AND effect_class='read')) AND lease_expires_at < datetime('now')").fetchall()
            conn.execute("UPDATE workflow_actions SET state='pending',lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE (state='claimed' OR (state='started' AND effect_class='read')) AND lease_expires_at < datetime('now')",(now,))
            for row in rows:self._append_event(conn,str(row['run_id']),"action_reclaimed",{"action_id":str(row['action_id'])})
        return len(rows)

    async def list_nonterminal_runs(self) -> list[dict[str, Any]]:
        with self.database.read_connection() as conn:
            rows = conn.execute("SELECT * FROM workflow_runs WHERE state NOT IN ('succeeded','failed','cancelled','needs_attention') ORDER BY updated_at,run_id").fetchall()
        return [self._run(row) for row in rows]

    async def list_actions_for_reconcile(self) -> list[dict[str, Any]]:
        with self.database.read_connection() as conn:
            rows = conn.execute("SELECT * FROM workflow_actions WHERE state IN ('started','outcome_unknown') ORDER BY updated_at,action_id").fetchall()
        return [self._action(row) for row in rows]

    async def record_reconciled_outcome(self, action_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
        """Persist evidence returned by an existing child job/intent/recovery lookup."""
        state = outcome.get("state")
        if state not in {"succeeded", "failed", "needs_attention", "outcome_unknown"}:
            raise RepositoryConflict("reconcile_outcome_invalid")
        result = outcome.get("result")
        if result is not None and not isinstance(result, dict):
            raise RepositoryConflict("reconcile_outcome_invalid")
        with self.database.transaction() as conn:
            row = conn.execute("SELECT * FROM workflow_actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None: raise RepositoryConflict("not_found")
            if str(row["state"]) not in {"started", "outcome_unknown"}: return self._action(row)
            now = utc_now()
            action_state = "completed" if state == "succeeded" else ("outcome_unknown" if state == "outcome_unknown" else "failed")
            conn.execute("UPDATE workflow_actions SET state=?,child_state=?,result_json=?,updated_at=? WHERE action_id=?", (action_state,state,_json(result) if result is not None else None,now,action_id))
            if state in {"failed", "needs_attention"}:
                conn.execute("UPDATE workflow_runs SET state='needs_attention',state_version=state_version+1,updated_at=? WHERE run_id=? AND state NOT IN ('succeeded','failed','cancelled','needs_attention')", (now,row["run_id"]))
            self._append_event(conn,str(row["run_id"]),"action_reconciled",{"action_id":action_id,"child_state":state})
            row=conn.execute("SELECT * FROM workflow_actions WHERE action_id=?",(action_id,)).fetchone()
        return self._action(row)

    async def create_wait(self, *, owner_subject: str, run_id: str, step_id: str, wait_kind: str, expected_state_version: int, response_schema: dict[str, Any], expires_at: str | None = None) -> dict[str, Any]:
        with self.database.transaction() as conn:
            run=conn.execute("SELECT state_version FROM workflow_runs WHERE owner_subject=? AND run_id=?",(owner_subject,run_id)).fetchone()
            if run is None: raise RepositoryConflict("not_found")
            if int(run['state_version']) != expected_state_version: raise RepositoryConflict("stale_workflow_state")
            wait_id=new_id('wfw'); schema_json=_json(response_schema,65_536)
            existing=conn.execute("SELECT * FROM workflow_waits WHERE run_id=? AND step_id=? AND wait_kind=? AND resolved_at IS NULL ORDER BY created_at DESC LIMIT 1",(run_id,step_id,wait_kind)).fetchone()
            if existing is not None:
                if (
                    int(existing["expected_state_version"]) == expected_state_version
                    and str(existing["response_schema_digest"]) == _digest(response_schema)
                    and existing["expires_at"] == expires_at
                ):
                    return self._wait(existing)
                raise RepositoryConflict("workflow_wait_conflict")
            conn.execute("INSERT INTO workflow_waits(wait_id,run_id,step_id,wait_kind,expected_state_version,response_schema_json,response_schema_digest,expires_at,resolved_at,resolution_json,created_at) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?)",(wait_id,run_id,step_id,wait_kind,expected_state_version,schema_json,_digest(response_schema),expires_at,utc_now()))
            self._append_event(conn,run_id,"wait_created",{"wait_id":wait_id,"expected_state_version":expected_state_version})
            row=conn.execute("SELECT * FROM workflow_waits WHERE wait_id=?",(wait_id,)).fetchone()
        return self._wait(row)

    async def resolve_wait(self, *, owner_subject: str, run_id: str, wait_id: str,
                           expected_state_version: int, response_schema_digest: str,
                           response: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], bool]:
        """Consume an input exactly once against its original run version/schema."""
        with self.database.transaction() as conn:
            run = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject, run_id)).fetchone()
            wait = conn.execute("SELECT * FROM workflow_waits WHERE wait_id=? AND run_id=?", (wait_id, run_id)).fetchone()
            if run is None or wait is None: raise RepositoryConflict("not_found")
            existing = wait["resolution_json"]
            if existing is not None:
                resolved = json.loads(existing)
                if resolved.get("idempotency_key") == idempotency_key and resolved.get("response") == response:
                    return self._wait(wait), True
                raise RepositoryConflict("idempotency_conflict")
            if int(run["state_version"]) != expected_state_version or int(wait["expected_state_version"]) != expected_state_version:
                raise RepositoryConflict("stale_workflow_state")
            if str(wait["response_schema_digest"]) != response_schema_digest:
                raise RepositoryConflict("wait_schema_mismatch")
            schema = json.loads(wait["response_schema_json"])
            if schema.get("type") == "object" and not isinstance(response, dict):
                raise RepositoryConflict("wait_response_invalid")
            required = schema.get("required", [])
            if not isinstance(required, list) or any(not isinstance(item, str) or item not in response for item in required):
                raise RepositoryConflict("wait_response_invalid")
            resolution = {"idempotency_key": idempotency_key, "response": response}
            now = utc_now()
            changed = conn.execute("UPDATE workflow_waits SET resolved_at=?,resolution_json=? WHERE wait_id=? AND resolved_at IS NULL", (now, _json(resolution), wait_id))
            if changed.rowcount != 1: raise RepositoryConflict("stale_workflow_state")
            self._append_event(conn, run_id, "wait_resolved", {"wait_id": wait_id, "state_version": expected_state_version})
            wait = conn.execute("SELECT * FROM workflow_waits WHERE wait_id=?", (wait_id,)).fetchone()
        return self._wait(wait), False

    async def create_step(self, *, owner_subject: str, run_id: str, step_id: str, attempt: int, kind: str,
                          input_ref: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        with self.database.transaction() as conn:
            if conn.execute("SELECT 1 FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject, run_id)).fetchone() is None: raise RepositoryConflict("not_found")
            row = conn.execute("SELECT * FROM workflow_steps WHERE run_id=? AND step_id=? AND attempt=?", (run_id, step_id, attempt)).fetchone()
            if row is not None:
                parsed = self._step(row)
                if parsed["kind"] == kind and parsed["input_ref"] == input_ref: return parsed, True
                raise RepositoryConflict("workflow_step_conflict")
            now=utc_now(); conn.execute("INSERT INTO workflow_steps(run_id,step_id,attempt,kind,state,state_version,input_ref_json,output_ref_json,error_code,created_at,updated_at) VALUES(?,?,?,?, 'pending',0,?,NULL,NULL,?,?)", (run_id,step_id,attempt,kind,_json(input_ref) if input_ref is not None else None,now,now))
            self._append_event(conn,run_id,"step_created",{"step_id":step_id,"attempt":attempt})
            row=conn.execute("SELECT * FROM workflow_steps WHERE run_id=? AND step_id=? AND attempt=?",(run_id,step_id,attempt)).fetchone()
        return self._step(row),False

    async def transition_step(self, *, owner_subject: str, run_id: str, step_id: str, attempt: int,
                              expected_state: str, expected_version: int, target: str,
                              output_ref: dict[str, Any] | None = None, error_code: str | None = None) -> dict[str, Any]:
        try: validate_step_transition(expected_state,target)
        except InvalidWorkflowTransition as error: raise RepositoryConflict("invalid_workflow_step_transition") from error
        with self.database.transaction() as conn:
            if conn.execute("SELECT 1 FROM workflow_runs WHERE owner_subject=? AND run_id=?",(owner_subject,run_id)).fetchone() is None: raise RepositoryConflict("not_found")
            row=conn.execute("SELECT * FROM workflow_steps WHERE run_id=? AND step_id=? AND attempt=?",(run_id,step_id,attempt)).fetchone()
            if row is None: raise RepositoryConflict("not_found")
            if str(row['state']) in {'succeeded','failed','skipped','cancelled','needs_attention'}: raise RepositoryConflict("terminal_immutable")
            if str(row['state']) != expected_state or int(row['state_version']) != expected_version: raise RepositoryConflict("stale_workflow_state")
            now=utc_now(); updated=conn.execute("UPDATE workflow_steps SET state=?,state_version=state_version+1,output_ref_json=?,error_code=?,updated_at=? WHERE run_id=? AND step_id=? AND attempt=? AND state=? AND state_version=?",(target,_json(output_ref) if output_ref is not None else None,error_code,now,run_id,step_id,attempt,expected_state,expected_version))
            if updated.rowcount != 1:raise RepositoryConflict("stale_workflow_state")
            self._append_event(conn,run_id,"step_state",{"step_id":step_id,"from":expected_state,"to":target})
            row=conn.execute("SELECT * FROM workflow_steps WHERE run_id=? AND step_id=? AND attempt=?",(run_id,step_id,attempt)).fetchone()
        return self._step(row)

    async def cancel_run(self, *, owner_subject: str, run_id: str, expected_state: str, expected_version: int) -> dict[str, Any]:
        """Cancellation never pretends an already-started write was cancelled."""
        with self.database.transaction() as conn:
            run = conn.execute("SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?", (owner_subject, run_id)).fetchone()
            if run is None: raise RepositoryConflict("not_found")
            started = conn.execute("SELECT 1 FROM workflow_actions WHERE run_id=? AND effect_class='write' AND state IN ('started','outcome_unknown')", (run_id,)).fetchone()
            target = "waiting_for_recovery" if started is not None else "cancelled"
            if str(run['state']) != expected_state or int(run['state_version']) != expected_version: raise RepositoryConflict("stale_workflow_state")
            if str(run['state']) in TERMINAL_RUN_STATES: raise RepositoryConflict("terminal_immutable")
            if target == 'cancelled': validate_run_transition(expected_state, target)
            now=utc_now(); conn.execute("UPDATE workflow_runs SET state=?,state_version=state_version+1,updated_at=? WHERE run_id=?",(target,now,run_id))
            conn.execute(
                "UPDATE workflow_actions SET state='cancelled',lease_owner=NULL,"
                "lease_expires_at=NULL,updated_at=? "
                "WHERE run_id=? AND state IN ('pending','claimed')",
                (now, run_id),
            )
            self._append_event(conn,run_id,"cancel_requested",{"result":target})
            run=conn.execute("SELECT * FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone()
        return self._run(run)

    async def security_revoke_run(
        self, *, owner_subject: str, run_id: str
    ) -> dict[str, Any]:
        with self.database.transaction() as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?",
                (owner_subject, run_id),
            ).fetchone()
            if run is None:
                raise RepositoryConflict("not_found")
            if str(run["state"]) in TERMINAL_RUN_STATES:
                return self._run(run)
            now = utc_now()
            conn.execute(
                "UPDATE workflow_actions SET state='cancelled',lease_owner=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE run_id=? "
                "AND state IN ('pending','claimed')",
                (now, run_id),
            )
            conn.execute(
                "UPDATE workflow_runs SET state='needs_attention',"
                "state_version=state_version+1,updated_at=? WHERE run_id=?",
                (now, run_id),
            )
            self._append_event(
                conn,
                run_id,
                "skill_security_revoked",
                {"previous_state": str(run["state"])},
            )
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._run(run)

    async def mark_run_needs_attention(
        self, *, owner_subject: str, run_id: str, reason: str
    ) -> dict[str, Any]:
        with self.database.transaction() as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE owner_subject=? AND run_id=?",
                (owner_subject, run_id),
            ).fetchone()
            if run is None:
                raise RepositoryConflict("not_found")
            if str(run["state"]) in TERMINAL_RUN_STATES:
                return self._run(run)
            now = utc_now()
            conn.execute(
                "UPDATE workflow_actions SET state='cancelled',lease_owner=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE run_id=? "
                "AND state IN ('pending','claimed')",
                (now, run_id),
            )
            conn.execute(
                "UPDATE workflow_runs SET state='needs_attention',"
                "state_version=state_version+1,updated_at=? WHERE run_id=?",
                (now, run_id),
            )
            self._append_event(
                conn,
                run_id,
                "workflow_needs_attention",
                {"previous_state": str(run["state"]), "reason": reason},
            )
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._run(run)

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
        value=dict(row)
        value['pins']=json.loads(value.pop('pins_json'))
        value['inputs']=json.loads(value.pop('inputs_json'))
        value['result']=json.loads(value.pop('result_json')) if value.get('result_json') else None
        value['error']=json.loads(value.pop('error_json')) if value.get('error_json') else None
        value['state_version']=int(value['state_version'])
        return value
    @staticmethod
    def _action(row: Any) -> dict[str, Any]:
        value=dict(row); value['payload']=json.loads(value.pop('payload_json')); value['result']=json.loads(value.pop('result_json')) if value.get('result_json') else None; value['child_ref']=json.loads(value.pop('child_ref_json')) if value.get('child_ref_json') else None; return value
    @staticmethod
    def _wait(row: Any) -> dict[str, Any]:
        value=dict(row)
        value['response_schema']=json.loads(value.pop('response_schema_json'))
        value['resolution']=json.loads(value.pop('resolution_json')) if value.get('resolution_json') else None
        return value
    @staticmethod
    def _event(row: Any) -> dict[str, Any]:
        value=dict(row); value['sequence']=int(value['sequence']); value['payload']=json.loads(value.pop('payload_json')); return value
    @staticmethod
    def _step(row: Any) -> dict[str, Any]:
        value=dict(row); value['attempt']=int(value['attempt']); value['state_version']=int(value['state_version']); value['input_ref']=json.loads(value.pop('input_ref_json')) if value.get('input_ref_json') else None; value['output_ref']=json.loads(value.pop('output_ref_json')) if value.get('output_ref_json') else None; return value
    @staticmethod
    def _command(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["expected_state_version"] = int(value["expected_state_version"])
        value["payload"] = json.loads(value.pop("payload_json"))
        value["result"] = (
            json.loads(value.pop("result_json"))
            if value.get("result_json")
            else None
        )
        return value
