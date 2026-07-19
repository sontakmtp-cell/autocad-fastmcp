"""Entry point: python -m autocad_mcp"""

from autocad_mcp.server import main

# Register optional feature modules on the shared FastMCP instance.
from autocad_mcp import auto_dimension_tool as _auto_dimension_tool  # noqa: F401,E402
from autocad_mcp import phase1_dimension_perf as _phase1_dimension_perf  # noqa: E402

_phase1_dimension_perf.install()

main()
