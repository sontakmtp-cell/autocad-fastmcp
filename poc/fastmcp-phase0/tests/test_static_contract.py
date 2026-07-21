"""Guard the adapter against accidental FastMCP private API coupling."""

from pathlib import Path


def test_adapter_does_not_use_fastmcp_private_api():
    source_root = Path(__file__).parents[1] / "src" / "fastmcp_phase0"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    assert "_tool_manager" not in source
    assert "fastmcp._" not in source
    assert "from fastmcp._" not in source
