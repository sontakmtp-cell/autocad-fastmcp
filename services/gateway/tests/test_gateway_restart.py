from __future__ import annotations

import pytest

from autocad_gateway.app import GatewayConfig
from autocad_gateway.composition import build_services


@pytest.mark.asyncio
async def test_restart_keeps_terminal_job_and_recovers_started_write(tmp_path):
    config = GatewayConfig(
        profile="phase3_poc",
        db_path=str(tmp_path / "restart.db"),
        fixture_tokens=(("device-a", "token-a"),),
    )
    first = build_services(config)
    await first.initialize()
    terminal = await first.repository.create_job(
        owner_subject=first.owner_subject,
        device_id="device-a",
        kind="observe",
        effect_class="read",
        payload={},
        idempotency_key="terminal",
        deadline_at=None,
    )
    await first.repository.transition_job(terminal["job_id"], "dispatched")
    await first.repository.transition_job(terminal["job_id"], "acknowledged")
    await first.repository.transition_job(terminal["job_id"], "running")
    snapshot = {
        "snapshot_id": "snapshot-before-restart",
        "document_revision": "revision-before-restart",
        "observation_level": "summary",
        "drawing": {},
        "entity_summary": {},
        "entities": [],
    }
    await first.repository.finalize_job_result(
        job_id=terminal["job_id"],
        device_id="device-a",
        command_id=terminal["command_id"],
        payload_hash=terminal["payload_hash"],
        target="succeeded",
        result={"snapshot": snapshot},
        snapshot=snapshot,
    )
    unknown = await first.repository.create_job(
        owner_subject=first.owner_subject,
        device_id="device-a",
        kind="write_fixture",
        effect_class="write",
        payload={},
        idempotency_key="unknown",
        deadline_at=None,
    )
    await first.repository.transition_job(unknown["job_id"], "dispatched")
    await first.repository.transition_job(unknown["job_id"], "acknowledged")
    await first.repository.transition_job(unknown["job_id"], "running")
    await first.shutdown()

    second = build_services(config)
    await second.initialize()
    try:
        assert (await second.repository.get_job(second.owner_subject, terminal["job_id"]))["state"] == "succeeded"
        assert await second.repository.get_snapshot(
            second.owner_subject, "snapshot-before-restart"
        )
        assert (await second.repository.get_job(second.owner_subject, unknown["job_id"]))["state"] == "outcome_unknown"
        events, _ = await second.repository.list_events(second.owner_subject, terminal["job_id"])
        assert events[-1]["state"] == "succeeded"
    finally:
        await second.shutdown()
