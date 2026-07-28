"""First-party, package-resource-only Phase 9 catalog loader."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

FORBIDDEN_KEYS = frozenset({"path", "url", "module", "class", "function", "command", "include", "plugin"})
ALLOWED_STEP_KINDS = frozenset({"observe", "query", "run_planner", "render_template", "prepare_program", "preview_program", "wait_user_input", "wait_program_revision", "request_commit", "wait_job", "validate_receipt", "branch", "emit_report", "request_rollback", "finish"})


class CatalogValidationError(ValueError):
    pass


def canonical_digest(domain: str, value: Any) -> str:
    body = json.dumps({"domain": domain, "payload": value}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _reject_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS:
                raise CatalogValidationError(f"forbidden execution field: {key}")
            _reject_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden(child)


def validate_skill_manifest(manifest: dict[str, Any]) -> None:
    _reject_forbidden(manifest)
    required = {"schema_version", "skill_id", "version", "title", "summary", "workflow_definition", "risk_floor", "assurance_floor", "budgets", "support_policy"}
    if not required <= set(manifest):
        raise CatalogValidationError("skill manifest missing required cad.skill/1 fields")
    if manifest["schema_version"] != "cad.skill/1":
        raise CatalogValidationError("unsupported skill schema")
    if manifest["risk_floor"] not in {"low", "medium"}:
        raise CatalogValidationError("skill risk floor is invalid")
    if manifest["assurance_floor"] != "user_recent_auth":
        raise CatalogValidationError("skill assurance floor is invalid")
    workflow = manifest["workflow_definition"]
    if not isinstance(workflow, dict) or not {"workflow_id", "version", "digest"} <= set(workflow):
        raise CatalogValidationError("workflow reference is not opaque and pinned")
    for field in ("guide_digest", "manifest_digest"):
        if not isinstance(manifest.get(field), str) or len(manifest[field]) != 71 or not manifest[field].startswith("sha256:"):
            raise CatalogValidationError(f"{field} is not a SHA-256 digest")


def validate_workflow_definition(definition: dict[str, Any]) -> None:
    _reject_forbidden(definition)
    if definition.get("schema_version") != "cad.workflow-definition/1":
        raise CatalogValidationError("unsupported workflow schema")
    steps = definition.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 64:
        raise CatalogValidationError("workflow steps must be bounded")
    ids = set()
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") not in ALLOWED_STEP_KINDS:
            raise CatalogValidationError("workflow step kind is not allowlisted")
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id or step_id in ids:
            raise CatalogValidationError("workflow step ids must be unique")
        ids.add(step_id)
        if not isinstance(step.get("depends_on", []), list):
            raise CatalogValidationError("workflow dependencies must be a list")
    if any(dep not in ids for step in steps for dep in step.get("depends_on", [])):
        raise CatalogValidationError("workflow dependency is unknown")


def asset_digests(manifest: dict[str, Any], workflow: dict[str, Any], guide: str) -> dict[str, str]:
    """Digest immutable package assets, omitting only the self-referential field."""
    manifest_without_digest = dict(manifest)
    manifest_without_digest.pop("manifest_digest", None)
    return {
        "workflow": canonical_digest("cad.workflow-definition/1", workflow),
        "guide": canonical_digest("cad.skill-guide/1", guide),
        "manifest": canonical_digest("cad.skill/1", manifest_without_digest),
    }


def load_catalog() -> dict[str, Any]:
    """Load only assets built into this package; callers cannot select a path."""
    root = resources.files(__package__)
    # Editable source checkouts keep release assets beside ``src``; wheels place
    # them inside the package via force-include.  Both are fixed package paths.
    if not root.joinpath("catalog.json").is_file():
        root = Path(__file__).resolve().parents[2]
    catalog = json.loads(root.joinpath("catalog.json").read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "cad.skill-catalog/1":
        raise CatalogValidationError("unsupported catalog schema")
    skills = []
    for item in catalog.get("skills", []):
        asset_dir = root.joinpath("skills", item["asset_id"], item["version"])
        manifest = json.loads(asset_dir.joinpath("skill.json").read_text(encoding="utf-8"))
        workflow = json.loads(asset_dir.joinpath("workflow.json").read_text(encoding="utf-8"))
        guide = asset_dir.joinpath("guide.md").read_text(encoding="utf-8")
        validate_skill_manifest(manifest)
        validate_workflow_definition(workflow)
        digests = asset_digests(manifest, workflow, guide)
        # The contracts/catalog integration pins and verifies manifest digests.
        # This asset-only loader verifies the referenced workflow and guide here;
        # manifest self-digest verification is performed by that shared contract.
        if manifest["workflow_definition"]["digest"] != digests["workflow"] or manifest["guide_digest"] != digests["guide"]:
            raise CatalogValidationError("packaged asset digest does not match immutable bytes")
        skills.append({"manifest": manifest, "workflow": workflow, "guide": guide})
    if len({(item["manifest"]["skill_id"], item["manifest"]["version"]) for item in skills}) != len(skills):
        raise CatalogValidationError("duplicate immutable skill version")
    return {"catalog": catalog, "skills": skills, "catalog_digest": canonical_digest("cad.skill-catalog/1", catalog)}
