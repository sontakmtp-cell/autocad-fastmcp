"""Add the immutable Phase 10 cleanup workflow without rewriting Phase 9 assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autocad_contracts.phase9_contracts import (
    canonical_skill_manifest_digest,
    canonical_workflow_definition_digest,
)

ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "drawing.cleanup-audit"
VERSION = "1.1.0"
AUTO_DIMENSION_SKILL_ID = "mechanical.auto-dimension-overall"
AUTO_DIMENSION_VERSION = "1.1.0"


def _dump(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest()


def _schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _result_schema() -> dict[str, object]:
    return _schema(
        {"result": {"type": "object", "properties": {}, "additionalProperties": False}}
    )


def _run_input(name: str) -> dict[str, str]:
    return {"kind": "run_input", "input_path": name}


def _ref(step_id: str) -> dict[str, str]:
    return {
        "kind": "step_output",
        "source_step_id": step_id,
        "output_path": "result",
    }


def _step(
    step_id: str,
    kind: str,
    depends_on: list[str],
    bindings: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "step_id": step_id,
        "kind": kind,
        "depends_on": depends_on,
        "input_bindings": bindings or {},
        "output_schema": _result_schema(),
        "timeout_seconds": 300,
        "retry_class": (
            "existing_idempotent"
            if kind in {"build_scene", "query_scene", "validate_scene"}
            else "deterministic"
            if kind in {"run_planner", "render_template"}
            else "none"
        ),
    }


def main() -> None:
    folder = ROOT / "skills" / SKILL_ID / VERSION
    folder.mkdir(parents=True, exist_ok=True)
    workflow = {
        "schema_version": "cad.workflow-definition/1",
        "workflow_id": SKILL_ID,
        "version": VERSION,
        "steps": [
            _step(
                "build_scene",
                "build_scene",
                [],
                {"source_snapshot_id": _run_input("source_snapshot_id")},
            ),
            _step(
                "query_scene",
                "query_scene",
                ["build_scene"],
                {"scene": _ref("build_scene")},
            ),
            _step(
                "validate_scene",
                "validate_scene",
                ["build_scene"],
                {"scene": _ref("build_scene")},
            ),
            _step(
                "report",
                "emit_report",
                ["query_scene", "validate_scene"],
                {
                    "issues": _ref("query_scene"),
                    "validation": _ref("validate_scene"),
                },
            ),
            _step(
                "review",
                "wait_user_input",
                ["report"],
                {"report": _ref("report")},
            ),
            _step("finish", "finish", ["review"]),
        ],
    }
    workflow["definition_digest"] = canonical_workflow_definition_digest(workflow)
    _dump(folder / "workflow.json", workflow)

    guide_digest = _sha((folder / "guide.md").read_bytes())
    input_fields = {
        "source_snapshot_id": {"type": "string", "minLength": 1},
        "document_revision": {"type": "string", "minLength": 1},
        "layer": {"type": "string", "minLength": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 200},
        "max_candidates": {"type": "integer", "minimum": 1, "maximum": 128},
    }
    manifest = {
        "schema_version": "cad.skill/1",
        "skill_id": SKILL_ID,
        "version": VERSION,
        "title": "Scene-backed cleanup audit",
        "summary": "Read-only cleanup audit over one immutable revision-pinned scene.",
        "domain": "drawing",
        "tags": ["phase10", "scene", "read-only"],
        "input_schema_ref": "input.schema.1",
        "output_schema_ref": "output.schema.1",
        "input_schema": _schema(input_fields),
        "output_schema": _schema(
            {
                "status": {"type": "string", "minLength": 1},
                "scene_digest": {"type": "string", "minLength": 71, "maxLength": 71},
            }
        ),
        "workflow_definition": {
            "workflow_id": SKILL_ID,
            "version": VERSION,
            "digest": workflow["definition_digest"],
        },
        "required_scopes": ["autocad.read"],
        "required_capabilities": ["scene.core/1"],
        "required_operation_packs": [],
        "risk_floor": "low",
        "assurance_floor": "none",
        "planner": None,
        "templates": [],
        "validation_profiles": ["scene.cleanup-audit/1"],
        "budgets": {"max_entities": 5000, "max_candidates": 128},
        "support_policy": {"mode": "dry_run"},
        "guide_digest": guide_digest,
    }
    manifest["manifest_digest"] = canonical_skill_manifest_digest(manifest)
    _dump(folder / "skill.json", manifest)

    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    catalog["skills"] = [
        item
        for item in catalog["skills"]
        if not (item["skill_id"] == SKILL_ID and item["version"] == VERSION)
    ] + [manifest]
    catalog["workflows"] = [
        item
        for item in catalog["workflows"]
        if not (item["workflow_id"] == SKILL_ID and item["version"] == VERSION)
    ] + [workflow]

    auto_folder = (
        ROOT / "skills" / AUTO_DIMENSION_SKILL_ID / AUTO_DIMENSION_VERSION
    )
    auto_folder.mkdir(parents=True, exist_ok=True)
    auto_workflow = {
        "schema_version": "cad.workflow-definition/1",
        "workflow_id": AUTO_DIMENSION_SKILL_ID,
        "version": AUTO_DIMENSION_VERSION,
        "steps": [
            _step(
                "build_scene",
                "build_scene",
                [],
                {"source_snapshot_id": _run_input("source_snapshot_id")},
            ),
            _step(
                "query_scene",
                "query_scene",
                ["build_scene"],
                {
                    "scene": _ref("build_scene"),
                    "entity_ids": _run_input("entity_ids"),
                },
            ),
            _step(
                "observe",
                "observe",
                ["query_scene"],
                {
                    "entity_ids": _run_input("entity_ids"),
                    "selection_evidence": _ref("query_scene"),
                },
            ),
            _step(
                "query",
                "query",
                ["observe"],
                {"snapshot": _ref("observe")},
            ),
            _step(
                "pure",
                "run_planner",
                ["query"],
                {
                    "entities": _ref("query"),
                    "selection_evidence": _ref("query_scene"),
                },
            ),
            _step(
                "prepare",
                "prepare_program",
                ["pure"],
                {"program": _ref("pure")},
            ),
            _step(
                "preview",
                "preview_program",
                ["prepare"],
                {"program": _ref("prepare")},
            ),
            _step(
                "review",
                "wait_user_input",
                ["preview"],
                {"preview": _ref("preview")},
            ),
            _step(
                "commit",
                "request_commit",
                ["review"],
                {"program": _ref("prepare")},
            ),
            _step(
                "job",
                "wait_job",
                ["commit"],
                {"intent": _ref("commit")},
            ),
            _step(
                "validate",
                "validate_receipt",
                ["job"],
                {"job": _ref("job")},
            ),
            _step("finish", "finish", ["validate"]),
        ],
    }
    auto_workflow["definition_digest"] = canonical_workflow_definition_digest(
        auto_workflow
    )
    _dump(auto_folder / "workflow.json", auto_workflow)
    auto_manifest = json.loads(
        (
            ROOT
            / "skills"
            / AUTO_DIMENSION_SKILL_ID
            / "1.0.0"
            / "skill.json"
        ).read_text(encoding="utf-8")
    )
    auto_manifest.update(
        {
            "version": AUTO_DIMENSION_VERSION,
            "title": "Scene-backed overall dimension",
            "summary": (
                "Overall dimension preview gated by immutable part and "
                "contour scene evidence."
            ),
            "tags": ["phase10", "scene", "write-gated"],
            "guide_digest": _sha((auto_folder / "guide.md").read_bytes()),
            "required_capabilities": sorted(
                set(auto_manifest["required_capabilities"]) | {"scene.core/1"}
            ),
            "workflow_definition": {
                "workflow_id": AUTO_DIMENSION_SKILL_ID,
                "version": AUTO_DIMENSION_VERSION,
                "digest": auto_workflow["definition_digest"],
            },
        }
    )
    auto_manifest["input_schema"]["properties"]["entity_ids"]["maxItems"] = 64
    auto_manifest["manifest_digest"] = canonical_skill_manifest_digest(
        auto_manifest
    )
    _dump(auto_folder / "skill.json", auto_manifest)
    catalog["skills"] = [
        item
        for item in catalog["skills"]
        if not (
            item["skill_id"] == AUTO_DIMENSION_SKILL_ID
            and item["version"] == AUTO_DIMENSION_VERSION
        )
    ] + [auto_manifest]
    catalog["workflows"] = [
        item
        for item in catalog["workflows"]
        if not (
            item["workflow_id"] == AUTO_DIMENSION_SKILL_ID
            and item["version"] == AUTO_DIMENSION_VERSION
        )
    ] + [auto_workflow]
    release = {
        "skills": catalog["skills"],
        "workflows": catalog["workflows"],
        # Phase 9 remains the default until the explicit Phase 10 workflow flag
        # and SceneApplicationService port are wired by composition.
        "channels": catalog["channels"],
    }
    release["release_digest"] = _sha(
        json.dumps(
            release, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    )
    release["assets"] = {
        path.relative_to(ROOT).as_posix(): _sha(path.read_bytes())
        for path in sorted(ROOT.glob("skills/*/*/*"))
    }
    _dump(ROOT / "catalog.json", release)


if __name__ == "__main__":
    main()
