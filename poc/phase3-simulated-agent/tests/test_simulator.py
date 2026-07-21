from __future__ import annotations

import inspect

import autocad_phase3_sim_agent
from autocad_phase3_sim_agent.agent import SimulatedAgent
from autocad_phase3_sim_agent.scenarios import validate_scenario


def test_scenarios_are_explicit_and_agent_has_no_gateway_import():
    assert validate_scenario("success") == "success"
    assert "autocad_gateway" not in inspect.getsource(SimulatedAgent)
    assert "autocad_mcp" not in inspect.getsource(SimulatedAgent)
    assert len(autocad_phase3_sim_agent.SCENARIOS) >= 10
