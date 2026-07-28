from copy import deepcopy

import pytest
from pydantic import ValidationError

from autocad_contracts import (
    canonical_skill_manifest_digest,
    canonical_workflow_definition_digest,
    canonical_workflow_wait_schema_digest,
    parse_skill_manifest,
    parse_workflow_definition,
    validate_json_schema_subset,
)


def workflow_payload():
    value = {
        "schema_version": "cad.workflow-definition/1",
        "workflow_id": "mechanical.plate-hole-pattern",
        "version": "1.0.0",
        "steps": [{"step_id": "finish", "kind": "finish", "depends_on": [], "input_bindings": {}, "output_schema": {"type": "object", "additionalProperties": False}, "timeout_seconds": 60}],
    }
    value["definition_digest"] = canonical_workflow_definition_digest(value)
    return value


def skill_payload():
    workflow = workflow_payload()
    value = {
        "schema_version": "cad.skill/1", "skill_id": "mechanical.plate-hole-pattern", "version": "1.0.0",
        "title": "Plate", "summary": "Bounded plate.", "domain": "mechanical", "tags": ["plate"],
        "input_schema_ref": "skill-input.plate", "output_schema_ref": "skill-output.plate",
        "input_schema": {"type": "object", "properties": {"width": {"type": "string"}}, "additionalProperties": False},
        "output_schema": {"type": "object", "additionalProperties": False},
        "workflow_definition": {"workflow_id": workflow["workflow_id"], "version": workflow["version"], "digest": workflow["definition_digest"]},
        "required_scopes": ["autocad.write"], "required_capabilities": [], "required_operation_packs": [],
        "risk_floor": "medium", "assurance_floor": "user_recent_auth", "planner": None, "templates": [],
        "validation_profiles": [], "budgets": {}, "support_policy": {},
        "guide_digest": "sha256:" + "a" * 64,
    }
    value["manifest_digest"] = canonical_skill_manifest_digest(value)
    return value


def test_skill_and_workflow_are_strict_and_domain_separated():
    workflow = parse_workflow_definition(workflow_payload())
    skill = parse_skill_manifest(skill_payload())
    assert workflow.definition_digest.startswith("sha256:")
    assert skill.manifest_digest != workflow.definition_digest
    unsafe = deepcopy(skill_payload())
    unsafe["path"] = "C:/unsafe"
    with pytest.raises(ValidationError):
        parse_skill_manifest(unsafe)


def test_workflow_rejects_cycles_and_wait_schema_is_pinned():
    payload = workflow_payload()
    payload["steps"] = [
        {"step_id": "a", "kind": "branch", "depends_on": ["b"], "input_bindings": {}, "condition": {"left": {"source_step_id": "b", "output_path": "state"}, "operator": "eq", "value": "ok"}, "output_schema": {}, "timeout_seconds": 1},
        {"step_id": "b", "kind": "finish", "depends_on": ["a"], "input_bindings": {}, "output_schema": {}, "timeout_seconds": 1},
    ]
    payload["definition_digest"] = canonical_workflow_definition_digest(payload)
    with pytest.raises(ValidationError, match="cycle"):
        parse_workflow_definition(payload)
    schema = {"type": "object", "additionalProperties": False}
    assert canonical_workflow_wait_schema_digest(schema).startswith("sha256:")
    with pytest.raises(ValueError, match="forbidden"):
        validate_json_schema_subset({"$ref": "https://unsafe.invalid/schema"})
