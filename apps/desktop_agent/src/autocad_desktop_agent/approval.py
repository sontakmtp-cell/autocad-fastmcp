"""Durable, local-only Phase 7 approval inbox."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autocad_contracts.agent_protocol import (
    ApprovalDecisionMessage,
    ApprovalRequestMessage,
    message_dict,
    parse_agent_message,
)


class ApprovalConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredApproval:
    request: ApprovalRequestMessage
    status: str
    decision: ApprovalDecisionMessage | None = None


class ApprovalStore:
    """Persist bounded requests so closing/reopening only the UI loses nothing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS phase7_local_approvals (
                    approval_request_digest TEXT PRIMARY KEY,
                    approval_request_id TEXT NOT NULL,
                    consent_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'approved', 'denied', 'invalidated', 'expired')
                    ),
                    decision_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_phase7_local_approvals_pending
                ON phase7_local_approvals(status, consent_id)
                """
            )

    def record_request(self, request: ApprovalRequestMessage) -> tuple[StoredApproval, bool]:
        encoded = json.dumps(
            message_dict(request),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT request_json, status, decision_json
                FROM phase7_local_approvals
                WHERE approval_request_digest = ?
                """,
                (request.approval_request_digest,),
            ).fetchone()
            if existing is not None:
                stored = self._row(existing)
                if stored.request.approval_request_id != request.approval_request_id:
                    raise ApprovalConflict("approval request digest collision")
                return stored, True
            connection.execute(
                """
                UPDATE phase7_local_approvals
                SET status = 'invalidated', updated_at = ?
                WHERE consent_id = ? AND status = 'pending'
                """,
                (now, request.consent_id),
            )
            connection.execute(
                """
                INSERT INTO phase7_local_approvals (
                    approval_request_digest, approval_request_id, consent_id,
                    request_json, status, decision_json, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', NULL, ?)
                """,
                (
                    request.approval_request_digest,
                    request.approval_request_id,
                    request.consent_id,
                    encoded,
                    now,
                ),
            )
        return StoredApproval(request=request, status="pending"), False

    def get(self, approval_request_id: str) -> StoredApproval | None:
        self.expire()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_json, status, decision_json
                FROM phase7_local_approvals
                WHERE approval_request_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (approval_request_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def pending(self) -> tuple[StoredApproval, ...]:
        self.expire()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_json, status, decision_json
                FROM phase7_local_approvals
                WHERE status = 'pending'
                ORDER BY updated_at, approval_request_id
                LIMIT 64
                """
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def record_decision(
        self,
        decision: ApprovalDecisionMessage,
    ) -> tuple[StoredApproval, bool]:
        encoded = json.dumps(
            message_dict(decision),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        target_status = "approved" if decision.decision == "approve" else "denied"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_json, status, decision_json
                FROM phase7_local_approvals
                WHERE approval_request_digest = ?
                """,
                (decision.approval_request_digest,),
            ).fetchone()
            if row is None:
                raise ApprovalConflict("approval request is unavailable")
            stored = self._row(row)
            if stored.status != "pending":
                if stored.status == target_status and stored.decision == decision:
                    return stored, True
                raise ApprovalConflict("approval request is no longer pending")
            request = stored.request
            expected = (
                request.approval_request_id,
                request.session_id,
                request.device_id,
                request.intent_id,
                request.consent_id,
                request.intent_digest,
                request.challenge_nonce,
                request.device_identity_generation,
                request.device_key_thumbprint,
            )
            actual = (
                decision.approval_request_id,
                decision.session_id,
                decision.device_id,
                decision.intent_id,
                decision.consent_id,
                decision.intent_digest,
                decision.challenge_nonce,
                decision.device_identity_generation,
                decision.device_key_thumbprint,
            )
            if actual != expected:
                raise ApprovalConflict("approval decision binding mismatch")
            connection.execute(
                """
                UPDATE phase7_local_approvals
                SET status = ?, decision_json = ?, updated_at = ?
                WHERE approval_request_digest = ? AND status = 'pending'
                """,
                (
                    target_status,
                    encoded,
                    now,
                    decision.approval_request_digest,
                ),
            )
        return StoredApproval(request=request, status=target_status, decision=decision), False

    def invalidate_pending(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE phase7_local_approvals
                SET status = 'invalidated', updated_at = ?
                WHERE status = 'pending'
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )
        return cursor.rowcount

    def expire(self) -> int:
        now = datetime.now(timezone.utc)
        expired: list[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT approval_request_digest, request_json
                FROM phase7_local_approvals
                WHERE status = 'pending'
                """
            ).fetchall()
            for digest, encoded in rows:
                try:
                    request = parse_agent_message(encoded)
                except ValueError:
                    expired.append(str(digest))
                    continue
                if (
                    not isinstance(request, ApprovalRequestMessage)
                    or datetime.fromisoformat(request.expires_at) <= now
                ):
                    expired.append(str(digest))
            if expired:
                connection.executemany(
                    """
                    UPDATE phase7_local_approvals
                    SET status = 'expired', updated_at = ?
                    WHERE approval_request_digest = ? AND status = 'pending'
                    """,
                    [
                        (datetime.now(timezone.utc).isoformat(), digest)
                        for digest in expired
                    ],
                )
        return len(expired)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5, isolation_level=None)

    @staticmethod
    def _row(row: sqlite3.Row | tuple[str, str, str | None]) -> StoredApproval:
        request = parse_agent_message(row[0])
        if not isinstance(request, ApprovalRequestMessage):
            raise ApprovalConflict("stored approval request is invalid")
        decision = None
        if row[2] is not None:
            parsed = parse_agent_message(row[2])
            if not isinstance(parsed, ApprovalDecisionMessage):
                raise ApprovalConflict("stored approval decision is invalid")
            decision = parsed
        return StoredApproval(request=request, status=str(row[1]), decision=decision)
