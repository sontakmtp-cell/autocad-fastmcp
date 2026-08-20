from __future__ import annotations

import autocad_contracts.agent_protocol as agent_protocol
from autocad_contracts import ResultMessage

from autocad_desktop_agent.observe_detail_transport import DETAIL_OBSERVE_MESSAGE_BYTES


def test_desktop_agent_installs_bounded_large_result_limit() -> None:
    assert agent_protocol.MAX_WEBSOCKET_MESSAGE_BYTES == DETAIL_OBSERVE_MESSAGE_BYTES
    assert DETAIL_OBSERVE_MESSAGE_BYTES == 16 * 1024 * 1024


def test_result_message_can_carry_multi_megabyte_detail_snapshot() -> None:
    entities = [
        {
            "entity_id": f"{index + 1:X}",
            "entity_type": "TEXT",
            "layer": "0",
            "geometry_status": "unsupported",
            "geometry_reason": "entity_type_unsupported",
            "padding": "x" * 512,
        }
        for index in range(3_000)
    ]
    message = ResultMessage(
        session_id="session-test",
        device_id="device-test",
        job_id="job-test",
        command_id="command-test",
        sequence=1,
        status="succeeded",
        payload_hash="0" * 64,
        result={
            "snapshot": {
                "observation_level": "detail",
                "entities": entities,
            }
        },
    )
    encoded = agent_protocol.canonical_json(
        message.model_dump(mode="json", exclude_none=True)
    ).encode("utf-8")
    assert len(encoded) > 1_048_576
    assert len(encoded) < DETAIL_OBSERVE_MESSAGE_BYTES
