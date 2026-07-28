from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

for source in (
    ROOT / "packages" / "contracts" / "src",
    ROOT / "services" / "gateway" / "src",
    ROOT / "services" / "gateway" / "tests",
    ROOT / "apps" / "desktop_agent" / "src",
    ROOT / "packages" / "cad_core" / "src",
    ROOT / "src",
):
    value = str(source)
    if value not in sys.path:
        sys.path.insert(0, value)
