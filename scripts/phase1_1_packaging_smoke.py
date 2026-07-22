
"""Clean-install Phase 1.1 wheels outside the source checkout."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


ROOT_SMOKE = r"""
import asyncio
from pathlib import Path
import sys

import autocad_mcp
import cad_core
from cad_core import CadApplicationService, CommandResult

class FakePort:
    async def get_status(self): return CommandResult(ok=True, payload={"backend": "fake"})
    async def health(self): return CommandResult(ok=True, payload={"backend": "fake"})
    async def get_drawing_info(self): return CommandResult(ok=True, payload={"entity_count": 0})
    async def list_entities(self, *, layer=None): return CommandResult(ok=True, payload={"entities": [], "count": 0})
    async def get_entity(self, *, entity_id): return CommandResult(ok=True, payload={"id": entity_id})
    async def list_layers(self): return CommandResult(ok=True, payload={"layers": []})
    async def get_screenshot(self): return CommandResult(ok=False, error="not supported")
    async def call(self, operation, *args): return CommandResult(ok=True, payload={"operation": operation})
    async def reinitialize(self): return CommandResult(ok=True, payload={"initialized": True})

async def main():
    service = CadApplicationService(FakePort())
    result = await service.get_drawing_info()
    assert result.ok and result.payload["entity_count"] == 0

asyncio.run(main())
prefix = Path(sys.prefix).resolve()
for module in (autocad_mcp, cad_core):
    module_path = Path(module.__file__).resolve()
    assert prefix in module_path.parents, (prefix, module_path)
"""

CORE_SMOKE = r"""
import asyncio
import importlib.util
from pathlib import Path
import sys

import cad_core
from cad_core import CadApplicationService, CadInvocation, CommandResult

for name in ("autocad_mcp", "mcp", "fastmcp", "starlette", "win32com", "pythoncom"):
    assert importlib.util.find_spec(name) is None, name

class FakePort:
    def __init__(self): self.fallback = []
    async def get_status(self): return CommandResult(ok=True, payload={"backend": "fake"})
    async def health(self): return CommandResult(ok=True, payload={"backend": "fake"})
    async def get_drawing_info(self): return CommandResult(ok=True, payload={"entity_count": 0})
    async def list_entities(self, *, layer=None): return CommandResult(ok=True, payload={"entities": [], "count": 0})
    async def get_entity(self, *, entity_id): return CommandResult(ok=True, payload={"id": entity_id})
    async def list_layers(self): return CommandResult(ok=True, payload={"layers": []})
    async def get_screenshot(self): return CommandResult(ok=False, error="not supported")
    async def call(self, operation, *args):
        self.fallback.append((operation, args))
        return CommandResult(ok=True, payload={"operation": operation})
    async def reinitialize(self): return CommandResult(ok=True, payload={"initialized": True})

async def main():
    port = FakePort()
    service = CadApplicationService(port)
    typed = await service.list_entities(layer="0")
    fallback = await service.execute(CadInvocation("drawing", "create", {"data": {"name": "clean"}}))
    assert typed.ok and fallback.result.ok
    assert port.fallback == [("drawing_create", ("clean",))]

asyncio.run(main())
module_path = Path(cad_core.__file__).resolve()
assert Path(sys.prefix).resolve() in module_path.parents
"""


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    artifacts = args.artifact_dir.resolve()
    root_wheels = sorted(artifacts.glob("autocad_mcp-*.whl"))
    core_wheels = sorted(artifacts.glob("autocad_cad_core-*.whl"))
    if len(root_wheels) != 1 or len(core_wheels) != 1:
        raise SystemExit(f"expected one root and one core wheel: {root_wheels} / {core_wheels}")

    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    clean_env["PYTHONNOUSERSITE"] = "1"

    with tempfile.TemporaryDirectory(prefix="phase1-1-wheel-smoke-") as temp:
        temp_root = Path(temp)
        cwd = temp_root / "outside-source"
        cwd.mkdir()
        wheelhouse = temp_root / "wheelhouse"
        wheelhouse.mkdir()
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--dest",
                str(wheelhouse),
                "--find-links",
                str(artifacts),
                "--only-binary=:all:",
                str(root_wheels[0]),
            ],
            cwd=cwd,
            env=clean_env,
        )

        root_env = temp_root / "root-env"
        venv.EnvBuilder(with_pip=True, clear=True).create(root_env)
        root_python = venv_python(root_env)
        run(
            [
                str(root_python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "autocad-mcp==3.0.0",
            ],
            cwd=cwd,
            env=clean_env,
        )
        run([str(root_python), "-c", ROOT_SMOKE], cwd=cwd, env=clean_env)

        core_env = temp_root / "core-env"
        venv.EnvBuilder(with_pip=True, clear=True).create(core_env)
        core_python = venv_python(core_env)
        run(
            [
                str(core_python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(artifacts),
                "autocad-cad-core==0.1.0",
            ],
            cwd=cwd,
            env=clean_env,
        )
        run([str(core_python), "-c", CORE_SMOKE], cwd=cwd, env=clean_env)

    print("Phase 1.1 clean-install smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
