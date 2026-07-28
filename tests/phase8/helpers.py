from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from autocad_contracts import (
    build_execution_binding_v1,
    compile_cad_program_v1,
    seal_cad_program_v1,
)
from autocad_gateway.phase8_contract_adapter import (
    AutocadContractsPhase8Compiler,
    Phase8CompilerSettings,
)


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = (
    ROOT
    / "packages"
    / "host_contracts"
    / "program"
    / "golden"
    / "cad-program-1.0-compiler-vector.json"
)


def golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def compile_golden():
    fixture = golden()
    source = seal_cad_program_v1(deepcopy(fixture["source"]))
    plan = compile_cad_program_v1(
        source,
        deepcopy(fixture["plan"]["execution_pins"]),
        compiler_package_hash=fixture["plan"]["compiler"]["compiler_package_hash"],
    )
    binding = build_execution_binding_v1(plan)
    return fixture, source, plan, binding


class CanonicalCompilerAdapter:
    """Use the production Gateway adapter with the checked-in trusted pins."""

    def compile(self, source: dict[str, Any]):
        fixture = golden()
        pins = fixture["plan"]["execution_pins"]
        compiler = AutocadContractsPhase8Compiler(
            Phase8CompilerSettings(
                compiler_package_hash=fixture["plan"]["compiler"][
                    "compiler_package_hash"
                ],
                runtime_id=pins["runtime_id"],
                host_family=pins["host_family"],
                host_version=pins["host_version"],
                package_id=pins["package_id"],
                package_version=pins["package_version"],
                package_hash=pins["package_hash"],
                capability_manifest_hash=pins["capability_manifest_hash"],
                operation_registry_version=pins["operation_registry_version"],
                operation_registry_hash=pins["operation_registry_hash"],
                policy_version=pins["policy_version"],
                rollout_policy_digest=pins["rollout_policy_digest"],
            )
        )
        return compiler.compile(deepcopy(source))
