"""Strict, data-only contracts for the Phase 9 skill/workflow platform.

These contracts deliberately describe *what* an approved Gateway service may do;
they never name executable code, paths, URLs, commands, or MCP tools.
"""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_protocol import canonical_json

CAD_SKILL_SCHEMA_VERSION = "cad.skill/1"
CAD_WORKFLOW_DEFINITION_SCHEMA_VERSION = "cad.workflow-definition/1"
CAD_WORKFLOW_RUN_SCHEMA_VERSION = "cad.workflow-run/1"
CAD_WORKFLOW_EVENT_SCHEMA_VERSION = "cad.workflow-event/1"
CAD_WORKFLOW_WAIT_SCHEMA_VERSION = "cad.workflow-wait/1"
CAD_SKILL_PUBLICATION_SCHEMA_VERSION = "cad.skill-publication/1"

_ID = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
_DIGEST = r"^sha256:[0-9a-f]{64}$"
_MAX_JSON_BYTES = 65_536
_FORBIDDEN_EXECUTION_KEYS = {"path", "url", "uri", "module", "class", "function", "command", "plugin", "http", "code", "script", "sql", "eval"}


class Phase9Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class OpaqueCatalogRef(Phase9Model):
    ref_id: str = Field(pattern=_ID)
    version: str = Field(pattern=_SEMVER)
    digest: str = Field(pattern=_DIGEST)


class WorkflowReference(Phase9Model):
    workflow_id: str = Field(pattern=_ID)
    version: str = Field(pattern=_SEMVER)
    digest: str = Field(pattern=_DIGEST)


def _bounded_json(value: Any, label: str) -> Any:
    try:
        encoded = canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be canonical JSON") from error
    if len(encoded) > _MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds byte limit")
    return value


def _reject_executable_keys(value: Any) -> None:
    if isinstance(value, dict):
        if any(str(key).lower() in _FORBIDDEN_EXECUTION_KEYS for key in value):
            raise ValueError("execution payload contains forbidden field")
        for item in value.values(): _reject_executable_keys(item)
    elif isinstance(value, list):
        for item in value: _reject_executable_keys(item)


_SCHEMA_KEYS = {
    "$schema", "$id", "title", "description", "type", "properties", "required",
    "additionalProperties", "items", "enum", "const", "oneOf", "anyOf", "allOf",
    "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems",
    "pattern", "format", "default",
}
_FORBIDDEN_SCHEMA_KEYS = {"$ref", "$dynamicRef", "contentMediaType", "contentEncoding", "if", "then", "else", "not"}


def validate_json_schema_subset(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate the intentionally small, non-executable JSON Schema subset."""
    _bounded_json(schema, "JSON schema")
    _reject_executable_keys(schema)

    def visit(node: Any, depth: int = 0) -> None:
        if depth > 16:
            raise ValueError("JSON schema nesting exceeds limit")
        if isinstance(node, dict):
            unknown = set(node) - _SCHEMA_KEYS
            forbidden = set(node) & _FORBIDDEN_SCHEMA_KEYS
            if unknown or forbidden:
                raise ValueError("JSON schema contains forbidden keyword")
            for key, item in node.items():
                if key == "properties":
                    if not isinstance(item, dict) or len(item) > 64:
                        raise ValueError("properties must be a bounded object")
                    if any(not re.fullmatch(_ID, name) for name in item):
                        raise ValueError("schema property name is invalid")
                    for property_schema in item.values():
                        visit(property_schema, depth + 1)
                    continue
                elif key == "required":
                    if not isinstance(item, list) or len(item) > 64 or not all(isinstance(x, str) for x in item):
                        raise ValueError("required must be a bounded string list")
                elif key in {"oneOf", "anyOf", "allOf"}:
                    if not isinstance(item, list) or not 1 <= len(item) <= 8:
                        raise ValueError("schema combinator is invalid")
                visit(item, depth + 1)
        elif isinstance(node, list):
            if len(node) > 64:
                raise ValueError("JSON schema array exceeds limit")
            for item in node:
                visit(item, depth + 1)
    if not isinstance(schema, dict):
        raise ValueError("JSON schema must be an object")
    visit(schema)
    return schema


class WorkflowStep(Phase9Model):
    step_id: str = Field(pattern=_ID)
    kind: Literal["observe", "query", "run_planner", "render_template", "prepare_program", "preview_program", "wait_user_input", "wait_program_revision", "request_commit", "wait_job", "validate_receipt", "branch", "emit_report", "request_rollback", "finish"]
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    input_bindings: dict[str, Any] = Field(default_factory=dict, max_length=32)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(ge=1, le=86_400)
    retry_class: Literal["none", "safe", "deterministic", "existing_idempotent"] = "none"

    @model_validator(mode="after")
    def _validate_step(self) -> "WorkflowStep":
        if self.step_id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("step dependencies must be unique and cannot self-reference")
        _bounded_json(self.input_bindings, "step input bindings")
        _reject_executable_keys(self.input_bindings)
        validate_json_schema_subset(self.output_schema)
        return self


class WorkflowDefinition(Phase9Model):
    schema_version: Literal[CAD_WORKFLOW_DEFINITION_SCHEMA_VERSION] = CAD_WORKFLOW_DEFINITION_SCHEMA_VERSION
    workflow_id: str = Field(pattern=_ID)
    version: str = Field(pattern=_SEMVER)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=64)
    definition_digest: str = Field(pattern=_DIGEST)

    @model_validator(mode="before")
    @classmethod
    def _verify_raw_digest(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("definition_digest") != canonical_workflow_definition_digest(value):
            raise ValueError("workflow definition digest does not match canonical payload")
        return value

    @model_validator(mode="after")
    def _sealed_graph(self) -> "WorkflowDefinition":
        ids = [step.step_id for step in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("workflow step IDs must be unique")
        known = set(ids)
        if any(not set(step.depends_on) <= known for step in self.steps):
            raise ValueError("workflow dependency is missing")
        children = {step_id: 0 for step_id in ids}
        for step in self.steps:
            for dependency in step.depends_on:
                children[dependency] += 1
        if any(count > 4 for count in children.values()):
            raise ValueError("workflow fan-out exceeds limit")
        pending = {step.step_id: set(step.depends_on) for step in self.steps}
        complete: set[str] = set()
        while pending:
            ready = {key for key, deps in pending.items() if deps <= complete}
            if not ready:
                raise ValueError("workflow graph contains a cycle")
            complete.update(ready)
            for key in ready:
                pending.pop(key)
        return self


class SkillManifest(Phase9Model):
    schema_version: Literal[CAD_SKILL_SCHEMA_VERSION] = CAD_SKILL_SCHEMA_VERSION
    skill_id: str = Field(pattern=_ID)
    version: str = Field(pattern=_SEMVER)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1024)
    domain: str = Field(pattern=_ID)
    tags: list[str] = Field(default_factory=list, max_length=16)
    input_schema_ref: str = Field(pattern=_ID)
    output_schema_ref: str = Field(pattern=_ID)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    workflow_definition: WorkflowReference
    required_scopes: list[Literal["autocad.read", "autocad.write"]] = Field(default_factory=list, max_length=2)
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    required_operation_packs: list[str] = Field(default_factory=list, max_length=16)
    risk_floor: Literal["low", "medium", "high", "destructive"]
    assurance_floor: Literal["none", "device_local_confirmation", "user_recent_auth", "user_recent_auth_plus_device_local"]
    planner: OpaqueCatalogRef | None = None
    templates: list[OpaqueCatalogRef] = Field(default_factory=list, max_length=16)
    validation_profiles: list[str] = Field(default_factory=list, max_length=16)
    budgets: dict[str, Any] = Field(default_factory=dict)
    support_policy: dict[str, Any] = Field(default_factory=dict)
    guide_digest: str = Field(pattern=_DIGEST)
    manifest_digest: str = Field(pattern=_DIGEST)

    @model_validator(mode="before")
    @classmethod
    def _verify_raw_digest(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("manifest_digest") != canonical_skill_manifest_digest(value):
            raise ValueError("skill manifest digest does not match canonical payload")
        return value

    @model_validator(mode="after")
    def _sealed_skill(self) -> "SkillManifest":
        if len(set(self.tags)) != len(self.tags) or len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("skill lists must not contain duplicates")
        validate_json_schema_subset(self.input_schema)
        validate_json_schema_subset(self.output_schema)
        _bounded_json(self.budgets, "skill budgets")
        _bounded_json(self.support_policy, "skill support policy")
        _reject_executable_keys(self.budgets)
        _reject_executable_keys(self.support_policy)
        return self


class WorkflowRun(Phase9Model):
    schema_version: Literal[CAD_WORKFLOW_RUN_SCHEMA_VERSION] = CAD_WORKFLOW_RUN_SCHEMA_VERSION
    run_id: str = Field(pattern=_ID)
    owner_subject: str = Field(min_length=1, max_length=512)
    actor_issuer: str = Field(min_length=1, max_length=2048)
    actor_subject: str = Field(min_length=1, max_length=512)
    skill: OpaqueCatalogRef
    workflow: WorkflowReference
    catalog_epoch: int = Field(ge=0)
    policy_epoch: int = Field(ge=0)
    planner_registry_hash: str = Field(pattern=_DIGEST)
    planner_registry_version: str = Field(pattern=_ID)
    template_digests: list[str] = Field(default_factory=list, max_length=16)
    component_digests: list[str] = Field(default_factory=list, max_length=16)
    input_digest: str = Field(pattern=_DIGEST)
    device_id: str = Field(pattern=_ID)
    device_identity_generation: int = Field(ge=1)
    initial_snapshot_id: str | None = Field(default=None, pattern=_ID)
    initial_document_id: str | None = Field(default=None, pattern=_ID)
    initial_document_revision: str | None = Field(default=None, max_length=512)
    state: Literal["created", "running", "waiting_for_user", "waiting_for_program_revision", "waiting_for_trusted_approval", "waiting_for_job", "waiting_for_recovery", "paused", "succeeded", "failed", "cancelled", "needs_attention"]
    state_version: int = Field(ge=0)
    current_step_id: str | None = Field(default=None, pattern=_ID)
    child_program_id: str | None = Field(default=None, pattern=_ID)
    child_program_revision: int | None = Field(default=None, ge=1)
    child_preview_id: str | None = Field(default=None, pattern=_ID)
    child_intent_id: str | None = Field(default=None, pattern=_ID)
    child_job_id: str | None = Field(default=None, pattern=_ID)
    child_receipt_id: str | None = Field(default=None, pattern=_ID)
    child_recovery_id: str | None = Field(default=None, pattern=_ID)
    created_at: str = Field(min_length=20, max_length=64)
    updated_at: str = Field(min_length=20, max_length=64)
    expires_at: str | None = Field(default=None, min_length=20, max_length=64)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    audit_correlation_id: str | None = Field(default=None, pattern=_ID)


class WorkflowEvent(Phase9Model):
    schema_version: Literal[CAD_WORKFLOW_EVENT_SCHEMA_VERSION] = CAD_WORKFLOW_EVENT_SCHEMA_VERSION
    event_id: str = Field(pattern=_ID)
    run_id: str = Field(pattern=_ID)
    sequence: int = Field(ge=1)
    event_type: str = Field(pattern=_ID)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(min_length=20, max_length=64)

    @model_validator(mode="after")
    def _event_payload(self) -> "WorkflowEvent":
        _bounded_json(self.payload, "workflow event payload")
        return self


class WorkflowWait(Phase9Model):
    schema_version: Literal[CAD_WORKFLOW_WAIT_SCHEMA_VERSION] = CAD_WORKFLOW_WAIT_SCHEMA_VERSION
    wait_id: str = Field(pattern=_ID)
    run_id: str = Field(pattern=_ID)
    step_id: str = Field(pattern=_ID)
    kind: Literal["user_input", "program_revision", "trusted_approval", "job", "recovery"]
    expected_state_version: int = Field(ge=0)
    response_schema: dict[str, Any]
    response_schema_digest: str = Field(pattern=_DIGEST)

    @model_validator(mode="after")
    def _wait_digest(self) -> "WorkflowWait":
        validate_json_schema_subset(self.response_schema)
        if self.response_schema_digest != canonical_workflow_wait_schema_digest(self.response_schema):
            raise ValueError("wait response schema digest does not match canonical payload")
        return self


class SkillPublication(Phase9Model):
    schema_version: Literal[CAD_SKILL_PUBLICATION_SCHEMA_VERSION] = CAD_SKILL_PUBLICATION_SCHEMA_VERSION
    skill_id: str = Field(pattern=_ID)
    version: str = Field(pattern=_SEMVER)
    status: Literal["draft", "published", "deprecated", "withdrawn", "security_revoked"]
    catalog_release_digest: str = Field(pattern=_DIGEST)
    operator_subject: str = Field(pattern=_ID)
    created_at: str = Field(min_length=20, max_length=64)


def _domain_digest(domain: str, value: Any) -> str:
    return "sha256:" + sha256(canonical_json({"domain": domain, "payload": value}).encode("utf-8")).hexdigest()


def _without_digest(value: BaseModel | dict[str, Any], field: str) -> dict[str, Any]:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    payload.pop(field, None)
    return payload


def canonical_skill_manifest_digest(value: SkillManifest | dict[str, Any]) -> str:
    return _domain_digest("cad.skill/1", _without_digest(value, "manifest_digest"))


def canonical_workflow_definition_digest(value: WorkflowDefinition | dict[str, Any]) -> str:
    return _domain_digest("cad.workflow-definition/1", _without_digest(value, "definition_digest"))


def canonical_workflow_wait_schema_digest(schema: dict[str, Any]) -> str:
    validate_json_schema_subset(schema)
    return _domain_digest("cad.workflow-wait-schema/1", schema)


def canonical_workflow_event_digest(value: WorkflowEvent | dict[str, Any]) -> str:
    return _domain_digest("cad.workflow-event/1", value.model_dump(mode="json") if isinstance(value, BaseModel) else value)


def parse_skill_manifest(value: dict[str, Any]) -> SkillManifest:
    return SkillManifest.model_validate(value)


def parse_workflow_definition(value: dict[str, Any]) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(value)
