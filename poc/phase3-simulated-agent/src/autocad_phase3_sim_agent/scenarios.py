"""Deterministic failure-injection names accepted by the simulator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    delay_before_ack: float = 0.0
    delay_result: float = 0.0


SCENARIOS = frozenset(
    {
        "success",
        "delay_before_ack",
        "delay_result",
        "drop_before_ack",
        "drop_after_ack_before_start",
        "drop_after_start_before_result",
        "duplicate_ack",
        "duplicate_progress",
        "duplicate_result",
        "out_of_order_progress",
        "payload_hash_mismatch",
        "stale_heartbeat",
        "reconnect_not_started",
        "reconnect_started",
        "reconnect_terminal",
        "cancel_before_start",
        "cancel_while_running",
        "cancel_too_late",
    }
)


def validate_scenario(value: str) -> str:
    if value not in SCENARIOS:
        raise ValueError(f"unsupported simulator scenario: {value}")
    return value
