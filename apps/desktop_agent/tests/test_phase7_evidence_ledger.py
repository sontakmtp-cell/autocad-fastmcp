from __future__ import annotations

import sqlite3

import pytest

from autocad_desktop_agent.ledger import CommandLedger, LedgerConflict


PACKAGE = {
    "package_id": "autocad.host.managed",
    "version": "1.0.0",
    "sha256": "a" * 64,
}
TIMESTAMP = "2026-07-28T01:00:00+00:00"


def _ledger(tmp_path) -> CommandLedger:
    ledger = CommandLedger(tmp_path / "agent.db")
    ledger.record_received(
        command_id="command-1",
        job_id="job-1",
        idempotency_key="idem-1",
        payload_hash="b" * 64,
        package=PACKAGE,
        session_id="session-1",
        device_id="device-1",
        kind="program_commit",
        binding={"execution_digest": f"sha256:{'c' * 64}"},
    )
    return ledger


def test_evidence_is_append_only_exact_duplicate_idempotent_and_conflict_fails(tmp_path):
    ledger = _ledger(tmp_path)
    values = {
        "event_id": "event-1",
        "command_id": "command-1",
        "source": "agent",
        "source_sequence": 1,
        "milestone": "host_dispatch_started",
        "outcome": "observed",
        "summary": "Agent dispatched the exact Host command",
        "details": {"execution_digest": f"sha256:{'c' * 64}"},
        "source_timestamp": TIMESTAMP,
    }
    event, created = ledger.append_evidence(**values)
    assert created is True
    replay, created = ledger.append_evidence(**values)
    assert created is False
    assert replay == event

    with pytest.raises(LedgerConflict, match="evidence_conflict"):
        ledger.append_evidence(
            **{**values, "summary": "Conflicting content for the same source sequence"}
        )
    with pytest.raises(LedgerConflict, match="evidence_details_invalid"):
        ledger.append_evidence(
            **{
                **values,
                "event_id": "event-secret",
                "source_sequence": 2,
                "details": {"access_token": "must-not-be-persisted"},
            }
        )
    assert ledger.list_evidence("command-1") == [event]

    with pytest.raises(sqlite3.IntegrityError, match="execution_evidence_append_only"):
        with ledger._connection:
            ledger._connection.execute(
                "UPDATE execution_evidence SET summary = 'tampered' "
                "WHERE event_id = 'event-1'"
            )
    with pytest.raises(sqlite3.IntegrityError, match="execution_evidence_append_only"):
        with ledger._connection:
            ledger._connection.execute(
                "DELETE FROM execution_evidence WHERE event_id = 'event-1'"
            )


def test_reconcile_evidence_is_bounded_read_only_and_survives_restart(tmp_path):
    path = tmp_path / "agent.db"
    ledger = _ledger(tmp_path)
    ledger.transition("command-1", "accepted")
    ledger.transition("command-1", "started")
    ledger.append_evidence(
        event_id="event-1",
        command_id="command-1",
        source="host",
        source_sequence=7,
        milestone="transaction_opened",
        outcome="observed",
        summary="Host opened the exact transaction",
        source_timestamp=TIMESTAMP,
    )
    ledger.close()

    reopened = CommandLedger(path)
    status, entry, evidence = reopened.reconcile_evidence("command-1", "b" * 64)
    assert status == "started"
    assert entry is not None and entry.state == "started"
    assert len(evidence) == 1
    assert evidence[0].source == "host"
    assert reopened.get("command-1").state == "started"

    with pytest.raises(LedgerConflict, match="evidence_sequence_rejected"):
        reopened.append_evidence(
            event_id="event-old",
            command_id="command-1",
            source="host",
            source_sequence=6,
            milestone="host_admitted",
            outcome="observed",
            summary="Out-of-order evidence must not be appended",
            source_timestamp=TIMESTAMP,
        )
