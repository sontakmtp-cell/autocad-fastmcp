from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    canonical_execution_binding,
    canonical_execution_plan,
    canonical_source,
    compile_cad_program_v1,
    parse_cad_program_v1,
    parse_execution_binding_v1,
    parse_execution_plan_v1,
    seal_cad_program_v1,
    verify_execution_binding_v1,
)

from helpers import compile_golden, golden


def test_real_compiler_reproduces_checked_in_cross_runtime_golden_vector():
    fixture, source, plan, binding = compile_golden()

    assert canonical_source(source) == fixture["canonical_source"]
    assert source.semantic_digest == fixture["source_digest"]
    assert plan.compiler.model_dump(mode="json") == fixture["plan"]["compiler"]
    assert canonical_execution_plan(plan) == fixture["canonical_plan"]
    assert plan.execution_plan_digest == fixture["execution_plan_digest"]
    assert plan.expansion_digest == fixture["expansion_digest"]
    assert plan.effect_manifest_digest == fixture["effect_manifest_digest"]
    assert plan.target_refs_digest == fixture["target_refs_digest"]
    assert plan.validation_profiles_digest == fixture["validation_profiles_digest"]
    assert plan.checkpoint_strategy_digest == fixture["checkpoint_strategy_digest"]
    assert plan.hard_budgets_digest == fixture["hard_budgets_digest"]
    assert canonical_execution_binding(binding) == fixture[
        "canonical_execution_binding"
    ]
    assert binding.execution_binding_digest == fixture["execution_binding_digest"]
    verify_execution_binding_v1(binding, plan)


def test_real_compiler_is_byte_stable_across_repeated_runs():
    fixture = golden()
    results = []
    for _ in range(5):
        source = seal_cad_program_v1(deepcopy(fixture["source"]))
        plan = compile_cad_program_v1(
                source,
                deepcopy(fixture["plan"]["execution_pins"]),
                compiler_package_hash=fixture["plan"]["compiler"][
                    "compiler_package_hash"
                ],
        )
        results.append(plan.model_dump_json())

    assert len(set(results)) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"eval": "1+1"}),
        lambda value: value.update({"command": "_.ERASE ALL"}),
        lambda value: value.update({"path": r"C:\Users\operator\payload.dll"}),
        lambda value: value.update({"path": r"\\server\share\payload.dll"}),
        lambda value: value.update({"url": "https://attacker.invalid/payload"}),
        lambda value: value.update({"url": "file:///C:/payload.dll"}),
        lambda value: value.update({"environment": "USERPROFILE"}),
        lambda value: value.update({"script": "(command \"_.ERASE\" \"ALL\")"}),
        lambda value: value["operations"].__setitem__(
            1,
            {
                "operation_id": "unsafe",
                "kind": "load_assembly",
                "path": r"C:\payload.dll",
            },
        ),
        lambda value: value["operations"].__setitem__(
            1,
            {
                "operation_id": "unsafe",
                "kind": "move",
                "target": {"handle": "1A"},
            },
        ),
    ],
)
def test_arbitrary_code_command_path_network_and_raw_handles_are_rejected(
    mutation,
):
    payload = deepcopy(golden()["source"])
    payload.pop("semantic_digest", None)
    mutation(payload)

    with pytest.raises((ValidationError, ValueError)):
        seal_cad_program_v1(payload)


def test_source_plan_and_binding_reject_digest_preserving_extra_authority():
    fixture, _, _, _ = compile_golden()

    for key, value in (
        ("source", {"artifact_path": r"C:\payload.json"}),
        ("plan", {"command_name": "_.NETLOAD"}),
        ("execution_binding", {"restore_payload": {"raw_handle": "DEAD"}}),
    ):
        payload = deepcopy(fixture[key])
        payload.update(value)
        parser = {
            "source": parse_cad_program_v1,
            "plan": parse_execution_plan_v1,
            "execution_binding": parse_execution_binding_v1,
        }[key]
        with pytest.raises(ValidationError):
            parser(payload)


def test_expression_repeat_and_expansion_limits_fail_closed():
    fixture = golden()

    deep = deepcopy(fixture["source"])
    deep.pop("semantic_digest", None)
    expression = {"op": "variable", "name": "pitch"}
    for _ in range(20):
        expression = {"op": "abs", "operand": expression}
    deep["operations"][1]["end"]["x"] = expression
    with pytest.raises((ValidationError, ValueError), match="depth|nesting"):
        seal_cad_program_v1(deep)

    oversized = deepcopy(fixture["source"])
    oversized.pop("semantic_digest", None)
    oversized["operations"][1]["repeat"]["rows"]["value"]["value"] = "9"
    oversized["operations"][1]["repeat"]["columns"]["value"]["value"] = "9"
    sealed = seal_cad_program_v1(oversized)
    with pytest.raises(ValueError, match="repeat exceeds bound"):
        compile_cad_program_v1(
            sealed,
            deepcopy(fixture["plan"]["execution_pins"]),
            compiler_package_hash=fixture["plan"]["compiler"][
                "compiler_package_hash"
            ],
        )
