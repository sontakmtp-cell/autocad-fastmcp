"""Run the focused Phase 9 contract, durability, catalog, and workflow checks."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "packages/contracts/tests/test_phase9_contracts.py",
    "packages/contracts/tests/test_phase9_security_matrix.py",
    "services/gateway/tests/phase9",
    "tests/phase9/test_reference_workflows.py",
]


def main() -> int:
    basetemp = ROOT / ".pytest_cache" / "phase9-conformance"
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        str(ROOT / path)
        for path in ("packages/cad_core/src", "packages/contracts/src", "services/gateway/src")
    )
    command = [
        "uv", "run", "--project", "services/gateway", "--group", "test",
        "python", "-m", "pytest", "-q",
        f"--basetemp={basetemp}",
        *TARGETS,
    ]
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
