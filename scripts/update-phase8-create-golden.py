"""Regenerate the canonical create-only CAD Program 1.0 golden vector.

The source and trusted execution pins are intentionally read from the existing
checked-in vector.  Every derived compiler, plan, binding, and digest field is
recomputed through the public contracts package.
"""

from __future__ import annotations

import json
from pathlib import Path

from autocad_contracts import (
    build_execution_binding_v1,
    canonical_compiler_digest,
    canonical_compiler_manifest,
    canonical_execution_binding,
    canonical_execution_plan,
    canonical_hard_budgets,
    canonical_source,
    compile_cad_program_v1,
    seal_cad_program_v1,
)
from autocad_contracts.phase8_contracts import (
    COMPILER_DIGEST_DOMAIN,
    EFFECT_DIGEST_DOMAIN,
    EXECUTION_BINDING_DIGEST_DOMAIN,
    EXPANSION_DIGEST_DOMAIN,
    HARD_BUDGETS_DIGEST_DOMAIN,
    PLAN_DIGEST_DOMAIN,
    SOURCE_DIGEST_DOMAIN,
    TARGET_REFS_DIGEST_DOMAIN,
    VALIDATION_PROFILES_DIGEST_DOMAIN,
    CHECKPOINT_STRATEGY_DIGEST_DOMAIN,
)


ROOT = Path(__file__).resolve().parents[1]
VECTOR = (
    ROOT
    / "packages"
    / "host_contracts"
    / "program"
    / "golden"
    / "cad-program-1.0-compiler-vector.json"
)


def main() -> None:
    previous = json.loads(VECTOR.read_text(encoding="utf-8"))
    source_input = dict(previous["source"])
    source_input.pop("semantic_digest", None)
    source = seal_cad_program_v1(source_input)
    pins = previous["plan"]["execution_pins"]
    compiler_package_hash = previous["plan"]["compiler"]["compiler_package_hash"]
    plan = compile_cad_program_v1(
        source,
        pins,
        compiler_package_hash=compiler_package_hash,
    )
    binding = build_execution_binding_v1(plan)
    value = {
        "digest_domains": {
            "source": SOURCE_DIGEST_DOMAIN,
            "compiler": COMPILER_DIGEST_DOMAIN,
            "expansion": EXPANSION_DIGEST_DOMAIN,
            "effect": EFFECT_DIGEST_DOMAIN,
            "target_refs": TARGET_REFS_DIGEST_DOMAIN,
            "validation_profiles": VALIDATION_PROFILES_DIGEST_DOMAIN,
            "checkpoint_strategy": CHECKPOINT_STRATEGY_DIGEST_DOMAIN,
            "hard_budgets": HARD_BUDGETS_DIGEST_DOMAIN,
            "plan": PLAN_DIGEST_DOMAIN,
            "execution_binding": EXECUTION_BINDING_DIGEST_DOMAIN,
        },
        "source": source.model_dump(mode="json", exclude_none=True),
        "canonical_source": canonical_source(source),
        "source_digest": source.semantic_digest,
        "compiler_manifest": canonical_compiler_manifest(),
        "compiler_digest": canonical_compiler_digest(),
        "plan": plan.model_dump(mode="json", exclude_none=True),
        "expansion_digest": plan.expansion_digest,
        "effect_manifest_digest": plan.effect_manifest_digest,
        "target_refs_digest": plan.target_refs_digest,
        "validation_profiles_digest": plan.validation_profiles_digest,
        "checkpoint_strategy_digest": plan.checkpoint_strategy_digest,
        "hard_budgets": canonical_hard_budgets(plan.budgets),
        "hard_budgets_digest": plan.hard_budgets_digest,
        "canonical_plan": canonical_execution_plan(plan),
        "execution_plan_digest": plan.execution_plan_digest,
        "execution_binding": binding.model_dump(mode="json", exclude_none=True),
        "canonical_execution_binding": canonical_execution_binding(binding),
        "execution_binding_digest": binding.execution_binding_digest,
    }
    VECTOR.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
