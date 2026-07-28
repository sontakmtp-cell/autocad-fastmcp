from copy import deepcopy

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    canonical_skill_manifest_digest,
    canonical_workflow_definition_digest,
    parse_skill_manifest,
    parse_workflow_definition,
)


def _workflow() -> dict:
    value = {
        "schema_version": "cad.workflow-definition/1",
        "workflow_id": "mechanical.bounded",
        "version": "1.0.0",
        "steps": [
            {
                "step_id": "finish",
                "kind": "finish",
                "depends_on": [],
                "input_bindings": {},
                "output_schema": {"type": "object", "additionalProperties": False},
                "timeout_seconds": 60,
            }
        ],
    }
    value["definition_digest"] = canonical_workflow_definition_digest(value)
    return value


def _skill() -> dict:
    workflow = _workflow()
    value = {
        "schema_version": "cad.skill/1",
        "skill_id": "mechanical.bounded",
        "version": "1.0.0",
        "title": "Bounded",
        "summary": "A bounded first-party skill.",
        "domain": "mechanical",
        "tags": ["bounded"],
        "input_schema_ref": "skill-input.bounded",
        "output_schema_ref": "skill-output.bounded",
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object", "additionalProperties": False},
        "workflow_definition": {
            "workflow_id": workflow["workflow_id"],
            "version": workflow["version"],
            "digest": workflow["definition_digest"],
        },
        "required_scopes": ["autocad.write"],
        "required_capabilities": [],
        "required_operation_packs": [],
        "risk_floor": "medium",
        "assurance_floor": "user_recent_auth",
        "planner": None,
        "templates": [],
        "validation_profiles": [],
        "budgets": {},
        "support_policy": {},
        "guide_digest": "sha256:" + "a" * 64,
    }
    value["manifest_digest"] = canonical_skill_manifest_digest(value)
    return value


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "C:/unsafe"),
        ("url", "https://unsafe.invalid"),
        ("module", "unsafe.module"),
        ("function", "run"),
        ("command", "powershell"),
        ("approve", True),
        ("risk", "low"),
        ("capability", "spoofed"),
    ],
)
def test_skill_rejects_execution_and_trusted_authority_fields(field: str, value: object) -> None:
    payload = _skill()
    payload[field] = value
    with pytest.raises(ValidationError):
        parse_skill_manifest(payload)


@pytest.mark.parametrize(
    "kind",
    ["run_python", "run_command", "call_tool", "http_request", "load_plugin", "execute_lisp"],
)
def test_workflow_rejects_forbidden_execution_steps(kind: str) -> None:
    payload = _workflow()
    payload["steps"][0]["kind"] = kind
    payload["definition_digest"] = canonical_workflow_definition_digest(payload)
    with pytest.raises(ValidationError):
        parse_workflow_definition(payload)


def test_digest_pin_detects_immutable_manifest_mutation() -> None:
    payload = _skill()
    tampered = deepcopy(payload)
    tampered["summary"] = "Changed after publication."
    with pytest.raises(ValidationError, match="digest does not match"):
        parse_skill_manifest(tampered)
