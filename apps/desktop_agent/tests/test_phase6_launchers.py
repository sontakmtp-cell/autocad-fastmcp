from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_phase6_launchers_keep_managed_write_default_off():
    agent = (ROOT / "scripts" / "run-phase6-agent.ps1").read_text(
        encoding="utf-8"
    )
    gateway = (ROOT / "scripts" / "run-phase6-program-gateway.ps1").read_text(
        encoding="utf-8"
    )

    for script in (agent, gateway):
        assert "[switch]$EnableManagedWrite" in script
        assert (
            '$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = '
            'if ($EnableManagedWrite) { "1" } else { "0" }'
        ) in script
        assert '$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = "1"' not in script
    assert (
        '$env:AUTOCAD_AGENT_WRITE_LOCK_ENABLED = '
        'if ($EnableManagedWrite) { "1" } else { "0" }'
    ) in agent
    assert '$env:AUTOCAD_AGENT_WRITE_LOCK_ENABLED = "1"' not in agent
