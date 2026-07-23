from __future__ import annotations

import pytest

from autocad_desktop_agent.ledger import CommandLedger, LedgerConflict


PACKAGE = {"package_id": "autocad.lisp.drawing_info", "version": "3.3-c1", "sha256": "a" * 64}


def test_ledger_duplicate_replay_and_terminal_persist(tmp_path):
    ledger = CommandLedger(tmp_path / "agent.db")
    entry, created = ledger.record_received(
        command_id="command-1",
        job_id="job-1",
        idempotency_key="idem-1",
        payload_hash="b" * 64,
        package=PACKAGE,
        session_id="session-1",
        device_id="device-1",
    )
    assert created is True
    assert entry.state == "received"
    ledger.transition("command-1", "accepted")
    ledger.transition("command-1", "started")
    terminal = ledger.transition("command-1", "succeeded", result={"ok": True})
    assert ledger.reconcile_status("command-1", "b" * 64) == ("terminal", terminal)

    reopened = CommandLedger(tmp_path / "agent.db")
    assert reopened.get("command-1").result == {"ok": True}
    with pytest.raises(LedgerConflict, match="replay_payload_mismatch"):
        reopened.record_received(
            command_id="command-1",
            job_id="job-1",
            idempotency_key="idem-1",
            payload_hash="c" * 64,
            package=PACKAGE,
            session_id="session-2",
            device_id="device-1",
        )


def test_pause_and_sequence_survive_restart(tmp_path):
    path = tmp_path / "agent.db"
    ledger = CommandLedger(path)
    ledger.set_paused(True)
    assert ledger.next_sequence() == 1
    ledger.close()
    reopened = CommandLedger(path)
    assert reopened.is_paused() is True
    assert reopened.next_sequence() == 2
