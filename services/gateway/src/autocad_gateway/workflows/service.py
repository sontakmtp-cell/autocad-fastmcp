"""Phase 9 application facade; FastMCP calls this boundary, never child tools."""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from cad_core.phase9_workflows import (
    PLANNER_REGISTRY_DIGEST,
    TEMPLATE_REGISTRY_DIGEST,
    audit_cleanup,
)

from ..infrastructure.sqlite.repositories import RepositoryConflict
from ..skills.catalog import CatalogError, SkillCatalog
from ..skills.catalog_repository import CatalogLifecycleError, SkillCatalogRepository


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_CONTROL_ACTIONS = {
    "submit_input",
    "attach_program_revision",
    "resume",
    "retry_safe_step",
    "cancel",
}


class WorkflowServiceError(ValueError):
    pass


DeviceResolver = Callable[[str, str], Awaitable[dict[str, Any]]]
SnapshotResolver = Callable[[str, str, str], Awaitable[dict[str, Any]]]
ProgramRevisionResolver = Callable[[str, str, int], Awaitable[dict[str, Any]]]
WritePreviewExecutor = Callable[
    [str, str, str, str, dict[str, Any], str],
    Awaitable[dict[str, Any]],
]
CommitRequestExecutor = Callable[
    [str, str, str], Awaitable[dict[str, Any]]
]


class WorkflowApplicationService:
    """Owner/device-scoped catalog and durable run facade.

    Approval is deliberately absent. Effect execution is performed only by the
    existing Program/admission/job services through runner ports.
    """

    def __init__(
        self,
        repository: Any,
        catalog_repository: SkillCatalogRepository,
        catalog: SkillCatalog,
        *,
        enabled: bool,
        catalog_enabled: bool,
        policy_epoch: int,
        write_enabled: bool,
        allowlist: set[str] | None = None,
        enabled_skills: set[str] | None = None,
        device_resolver: DeviceResolver | None = None,
        snapshot_resolver: SnapshotResolver | None = None,
        program_revision_resolver: ProgramRevisionResolver | None = None,
        write_preview_executor: WritePreviewExecutor | None = None,
        commit_request_executor: CommitRequestExecutor | None = None,
    ) -> None:
        self.repository = repository
        self.catalog_repository = catalog_repository
        self.catalog = catalog
        self.enabled = enabled
        self.catalog_enabled = catalog_enabled
        self.policy_epoch = policy_epoch
        self.write_enabled = write_enabled
        self.allowlist = allowlist or set()
        self.enabled_skills = enabled_skills or set()
        self.device_resolver = device_resolver
        self.snapshot_resolver = snapshot_resolver
        self.program_revision_resolver = program_revision_resolver
        self.write_preview_executor = write_preview_executor
        self.commit_request_executor = commit_request_executor

    def initialize_catalog(self) -> None:
        if self.catalog_enabled:
            self.catalog_repository.import_catalog(self.catalog)

    async def list_skills(
        self,
        *,
        owner_subject: str,
        device_id: str | None = None,
        query: str | None = None,
        domain: str | None = None,
        tags: tuple[str, ...] = (),
        required_support: str | None = None,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        if cursor < 0 or not 1 <= limit <= 50:
            raise WorkflowServiceError("invalid_request")
        if not self.catalog_enabled:
            return {"skills": [], "next_cursor": None}
        context = await self._device_context(owner_subject, device_id)
        values: list[dict[str, Any]] = []
        needle = (query or "").strip().casefold()
        requested_tags = {tag.strip().casefold() for tag in tags if tag.strip()}
        for manifest in self.catalog.list():
            if not self._skill_enabled(manifest.skill_id):
                continue
            try:
                status = self.catalog_repository.get_status(
                    manifest.skill_id, manifest.version
                )
            except CatalogLifecycleError:
                continue
            if status in {"withdrawn", "security_revoked"}:
                continue
            if domain and manifest.domain != domain:
                continue
            if requested_tags and not requested_tags.issubset(
                {tag.casefold() for tag in manifest.tags}
            ):
                continue
            if needle and needle not in " ".join(
                (manifest.skill_id, manifest.title, manifest.summary, manifest.domain)
            ).casefold():
                continue
            support = self._support(manifest, status, context)
            if required_support and support.state != required_support:
                continue
            values.append(
                {
                    "skill_id": manifest.skill_id,
                    "version": manifest.version,
                    "default_version": self.catalog_repository.get_channel(
                        manifest.skill_id
                    )[0],
                    "title": manifest.title,
                    "summary": manifest.summary,
                    "domain": manifest.domain,
                    "tags": list(manifest.tags),
                    "status": status,
                    "support": support.state,
                    "support_reason": support.reason,
                    "required_capabilities": list(manifest.required_capabilities),
                    "required_operation_packs": list(
                        manifest.required_operation_packs
                    ),
                    "risk_floor": manifest.risk_floor,
                    "manifest_uri": (
                        f"cad://skills/{manifest.skill_id}/versions/"
                        f"{manifest.version}/manifest"
                    ),
                    "guide_uri": (
                        f"cad://skills/{manifest.skill_id}/versions/"
                        f"{manifest.version}/guide"
                    ),
                }
            )
        values.sort(key=lambda item: (item["skill_id"], item["version"]))
        page = values[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(values) else None
        return {"skills": page, "next_cursor": next_cursor}

    async def start(
        self,
        *,
        owner_subject: str,
        actor_subject: str,
        skill_id: str,
        version: str | None,
        device_id: str,
        source_snapshot_id: str | None,
        inputs: dict[str, Any],
        idempotency_key: str,
        scopes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not self.enabled:
            raise WorkflowServiceError("feature_disabled")
        self._bounded_id(device_id, "device_id")
        self._bounded_id(idempotency_key, "idempotency_key")
        try:
            manifest = self.catalog.resolve(skill_id, version)
            workflow = self.catalog.workflow_for(manifest)
            status = self.catalog_repository.get_status(
                manifest.skill_id, manifest.version
            )
        except (CatalogError, CatalogLifecycleError) as error:
            raise WorkflowServiceError("not_found") from error
        if not self._skill_enabled(manifest.skill_id) or status in {
            "withdrawn",
            "security_revoked",
        }:
            raise WorkflowServiceError("not_found")
        if any(scope not in scopes for scope in manifest.required_scopes):
            raise WorkflowServiceError("insufficient_scope")
        if "autocad.write" in manifest.required_scopes and not self.write_enabled:
            raise WorkflowServiceError("feature_disabled")
        _validate_schema(manifest.input_schema, inputs)
        input_snapshot = inputs.get("source_snapshot_id")
        if source_snapshot_id is not None and input_snapshot not in {
            None,
            source_snapshot_id,
        }:
            raise WorkflowServiceError("binding_mismatch")
        pinned_snapshot = source_snapshot_id or (
            input_snapshot if isinstance(input_snapshot, str) else None
        )
        device = await self._device_context(owner_subject, device_id)
        support = self._support(manifest, status, device)
        if support.state not in {"dry_run", "preview_only", "lab_commit", "certified"}:
            raise WorkflowServiceError("capability_missing")
        snapshot: dict[str, Any] | None = None
        if pinned_snapshot is not None:
            if self.snapshot_resolver is None:
                raise WorkflowServiceError("feature_disabled")
            try:
                snapshot = await self.snapshot_resolver(
                    owner_subject, device_id, pinned_snapshot
                )
            except Exception as error:
                raise WorkflowServiceError("not_found") from error
        planner_hash = (
            PLANNER_REGISTRY_DIGEST
            if manifest.planner is not None
            else TEMPLATE_REGISTRY_DIGEST
        )
        pins = {
            "skill_id": manifest.skill_id,
            "skill_version": manifest.version,
            "skill_digest": manifest.manifest_digest,
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.version,
            "workflow_digest": workflow.definition_digest,
            "catalog_epoch": self.catalog_repository.get_channel(
                manifest.skill_id
            )[1],
            "policy_epoch": self.policy_epoch,
            "planner_registry_version": "phase9-first-party/1",
            "planner_registry_hash": planner_hash,
        }
        try:
            run, replay = await self.repository.create_run(
                owner_subject=owner_subject,
                actor_issuer="opaque-owner-key/1",
                actor_subject=actor_subject,
                run_id="wfr:" + uuid.uuid4().hex,
                idempotency_key=idempotency_key,
                pins=pins,
                inputs=inputs,
                device_id=device_id,
                device_identity_generation=int(device["identity_generation"]),
                initial_snapshot_id=pinned_snapshot,
                initial_document_id=(
                    str(snapshot["document_id"])
                    if snapshot and snapshot.get("document_id")
                    else None
                ),
                initial_document_revision=(
                    str(snapshot["document_revision"]) if snapshot else None
                ),
            )
            if not replay:
                for step in workflow.steps:
                    await self.repository.create_step(
                        owner_subject=owner_subject,
                        run_id=run["run_id"],
                        step_id=step.step_id,
                        attempt=1,
                        kind=step.kind,
                        input_ref={
                            key: (
                                value.model_dump(mode="json")
                                if hasattr(value, "model_dump")
                                else value
                            )
                            for key, value in step.input_bindings.items()
                        },
                    )
                first = sorted(
                    (step for step in workflow.steps if not step.depends_on),
                    key=lambda step: step.step_id,
                )[0]
                await self.repository.transition_step(
                    owner_subject=owner_subject,
                    run_id=run["run_id"],
                    step_id=first.step_id,
                    attempt=1,
                    expected_state="pending",
                    expected_version=0,
                    target="ready",
                )
                run = await self.repository.transition_run(
                    owner_subject=owner_subject,
                    run_id=run["run_id"],
                    expected_state="created",
                    expected_version=0,
                    target="running",
                    current_step_id=first.step_id,
                    event_type="started",
                )
                if manifest.skill_id == "drawing.cleanup-audit":
                    run = await self._advance_cleanup_audit(
                        owner_subject=owner_subject,
                        run=run,
                        snapshot=snapshot or {},
                        inputs=inputs,
                    )
                elif "autocad.write" in manifest.required_scopes:
                    run = await self._advance_write_preview(
                        owner_subject=owner_subject,
                        run=run,
                        manifest=manifest,
                        snapshot=snapshot or {},
                        inputs=inputs,
                        idempotency_key=idempotency_key,
                    )
            return self._run_response(run, replay=replay)
        except RepositoryConflict as error:
            raise WorkflowServiceError(str(error)) from error

    async def get(
        self,
        owner_subject: str,
        run_id: str,
        *,
        event_cursor: int = 0,
        event_limit: int = 50,
    ) -> dict[str, Any]:
        if event_cursor < 0 or not 1 <= event_limit <= 100:
            raise WorkflowServiceError("invalid_request")
        run = await self.repository.get_run(owner_subject, run_id)
        if run is None:
            raise WorkflowServiceError("not_found")
        steps = await self.repository.list_steps(owner_subject, run_id)
        wait = await self.repository.current_wait(owner_subject, run_id)
        events = await self.repository.list_events(
            owner_subject, run_id, cursor=event_cursor, limit=event_limit
        )
        return {
            "run": run,
            "steps": steps,
            "current_wait": wait,
            "required_next_action": _required_next_action(run, wait),
            "events": events,
            "resource_uri": f"cad://workflows/{run_id}",
        }

    async def list_runs(
        self, owner_subject: str, *, cursor: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        runs = await self.repository.list_runs(
            owner_subject, cursor=cursor, limit=limit
        )
        return {
            "runs": runs,
            "next_cursor": cursor + limit if len(runs) == limit else None,
        }

    async def control(
        self,
        *,
        owner_subject: str,
        run_id: str,
        action: str,
        expected_state_version: int,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if action not in _CONTROL_ACTIONS:
            raise WorkflowServiceError("invalid_request")
        self._bounded_id(idempotency_key, "idempotency_key")
        run = await self.repository.get_run(owner_subject, run_id)
        if run is None:
            raise WorkflowServiceError("not_found")
        if run["state_version"] != expected_state_version:
            raise WorkflowServiceError("stale_workflow_state")
        try:
            if action == "cancel":
                result = await self.repository.cancel_run(
                    owner_subject=owner_subject,
                    run_id=run_id,
                    expected_state=run["state"],
                    expected_version=expected_state_version,
                )
                return self._run_response(result)
            if action == "submit_input":
                wait = await self.repository.current_wait(owner_subject, run_id)
                if wait is None or wait["wait_kind"] != "user_input":
                    raise WorkflowServiceError("invalid_workflow_state")
                response = payload or {}
                _validate_schema(wait["response_schema"], response)
                await self.repository.resolve_wait(
                    owner_subject=owner_subject,
                    run_id=run_id,
                    wait_id=wait["wait_id"],
                    expected_state_version=expected_state_version,
                    response_schema_digest=wait["response_schema_digest"],
                    response=response,
                    idempotency_key=idempotency_key,
                )
                if run["skill_id"] == "drawing.cleanup-audit":
                    result = await self._finish_cleanup_audit(
                        owner_subject=owner_subject, run=run
                    )
                    return self._run_response(result)
                if run["skill_id"] == "mechanical.auto-dimension-overall":
                    result = await self._request_auto_dimension_commit(
                        owner_subject=owner_subject,
                        run=run,
                        idempotency_key=idempotency_key,
                    )
                    return self._run_response(result)
            elif action == "attach_program_revision":
                if self.program_revision_resolver is None or not isinstance(
                    payload, dict
                ):
                    raise WorkflowServiceError("invalid_request")
                program_id = payload.get("program_id")
                revision = payload.get("program_revision")
                if not isinstance(program_id, str) or not isinstance(revision, int):
                    raise WorkflowServiceError("invalid_request")
                await self.program_revision_resolver(
                    owner_subject, program_id, revision
                )
            elif action == "retry_safe_step":
                raise WorkflowServiceError("feature_disabled")
            result = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run_id,
                expected_state=run["state"],
                expected_version=expected_state_version,
                target="running",
                current_step_id=run.get("current_step_id"),
                event_type=action,
            )
            return self._run_response(result)
        except RepositoryConflict as error:
            raise WorkflowServiceError(str(error)) from error

    def read_manifest(self, skill_id: str, version: str) -> str:
        manifest = self._published_manifest(skill_id, version)
        return manifest.model_dump_json()

    def read_guide(self, skill_id: str, version: str) -> str:
        self._published_manifest(skill_id, version)
        try:
            return self.catalog.read_guide(skill_id, version)
        except CatalogError as error:
            raise WorkflowServiceError("not_found") from error

    def _published_manifest(self, skill_id: str, version: str) -> Any:
        try:
            manifest = self.catalog.resolve(skill_id, version)
            status = self.catalog_repository.get_status(skill_id, version)
        except (CatalogError, CatalogLifecycleError) as error:
            raise WorkflowServiceError("not_found") from error
        if status in {"withdrawn", "security_revoked"}:
            raise WorkflowServiceError("not_found")
        return manifest

    async def _advance_cleanup_audit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        snapshot: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        entities = snapshot.get("entities")
        if not isinstance(entities, list):
            raise WorkflowServiceError("stale_snapshot")
        layer = inputs["layer"]
        selected = [
            entity
            for entity in entities
            if not layer or entity.get("layer") == layer
        ][: inputs["page_size"]]
        query = await self._complete_ready_step(
            owner_subject, run["run_id"], "query", {"result": selected}
        )
        del query
        await self._ready_step(owner_subject, run["run_id"], "pure")
        report = audit_cleanup(
            {
                "source_snapshot_id": run["initial_snapshot_id"],
                "document_revision": run["initial_document_revision"],
            },
            selected,
            max_candidates=inputs["max_candidates"],
        )
        await self._complete_ready_step(
            owner_subject, run["run_id"], "pure", {"result": report}
        )
        await self._ready_step(owner_subject, run["run_id"], "report")
        await self._complete_ready_step(
            owner_subject, run["run_id"], "report", {"result": report}
        )
        await self._ready_step(owner_subject, run["run_id"], "review")
        review = await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="review",
            attempt=1,
            expected_state="ready",
            expected_version=1,
            target="running",
        )
        review = await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="review",
            attempt=1,
            expected_state=review["state"],
            expected_version=review["state_version"],
            target="waiting",
            output_ref={"result": report},
        )
        del review
        waiting = await self.repository.transition_run(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            expected_state="running",
            expected_version=run["state_version"],
            target="waiting_for_user",
            current_step_id="review",
            event_type="cleanup_report_ready",
        )
        await self.repository.create_wait(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="review",
            wait_kind="user_input",
            expected_state_version=waiting["state_version"],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "const": "continue"}
                },
            },
        )
        return waiting

    async def _advance_write_preview(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        manifest: Any,
        snapshot: dict[str, Any],
        inputs: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if self.write_preview_executor is None:
            raise WorkflowServiceError("feature_disabled")
        try:
            output = await self.write_preview_executor(
                owner_subject,
                manifest.skill_id,
                run["device_id"],
                run["initial_snapshot_id"],
                inputs,
                idempotency_key,
            )
        except Exception as error:
            raise WorkflowServiceError(
                getattr(error, "code", "backend_error")
            ) from error
        step_ids = (
            ["observe", "query", "pure", "prepare", "preview"]
            if manifest.skill_id == "mechanical.auto-dimension-overall"
            else ["pure", "prepare", "preview"]
        )
        for step_id in step_ids:
            steps = {
                step["step_id"]: step
                for step in await self.repository.list_steps(
                    owner_subject, run["run_id"]
                )
            }
            if steps[step_id]["state"] == "pending":
                await self._ready_step(owner_subject, run["run_id"], step_id)
            await self._complete_ready_step(
                owner_subject,
                run["run_id"],
                step_id,
                {"result": output.get(step_id, {})},
            )
        if manifest.skill_id == "mechanical.auto-dimension-overall":
            await self._ready_step(owner_subject, run["run_id"], "review")
            review = await self.repository.transition_step(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                step_id="review",
                attempt=1,
                expected_state="ready",
                expected_version=1,
                target="running",
            )
            await self.repository.transition_step(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                step_id="review",
                attempt=1,
                expected_state="running",
                expected_version=review["state_version"],
                target="waiting",
                output_ref={"result": output["preview"]},
            )
            waiting = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                expected_state="running",
                expected_version=run["state_version"],
                target="waiting_for_user",
                current_step_id="review",
                event_type="preview_ready",
            )
            await self.repository.create_wait(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                step_id="review",
                wait_kind="user_input",
                expected_state_version=waiting["state_version"],
                response_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["decision"],
                    "properties": {
                        "decision": {"type": "string", "const": "continue"}
                    },
                },
            )
            return waiting
        return await self._request_commit(
            owner_subject=owner_subject,
            run=run,
            preview_id=str(output["preview"]["preview_id"]),
            idempotency_key=f"{idempotency_key}:commit",
        )

    async def _request_auto_dimension_commit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run["run_id"])
        }
        review = steps["review"]
        preview = (review.get("output_ref") or {}).get("result") or {}
        if not isinstance(preview.get("preview_id"), str):
            raise WorkflowServiceError("binding_mismatch")
        await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="review",
            attempt=1,
            expected_state="waiting",
            expected_version=review["state_version"],
            target="succeeded",
            output_ref=review["output_ref"],
        )
        running = await self.repository.transition_run(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            expected_state="waiting_for_user",
            expected_version=run["state_version"],
            target="running",
            current_step_id="commit",
            event_type="submit_input",
        )
        return await self._request_commit(
            owner_subject=owner_subject,
            run=running,
            preview_id=preview["preview_id"],
            idempotency_key=idempotency_key,
        )

    async def _request_commit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        preview_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if self.commit_request_executor is None:
            raise WorkflowServiceError("feature_disabled")
        await self._ready_step(owner_subject, run["run_id"], "commit")
        commit_step = await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="commit",
            attempt=1,
            expected_state="ready",
            expected_version=1,
            target="running",
        )
        try:
            intent = await self.commit_request_executor(
                owner_subject, preview_id, idempotency_key
            )
        except Exception as error:
            raise WorkflowServiceError(
                getattr(error, "code", "backend_error")
            ) from error
        await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="commit",
            attempt=1,
            expected_state="running",
            expected_version=commit_step["state_version"],
            target="waiting",
            output_ref={"result": intent},
        )
        waiting = await self.repository.transition_run(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            expected_state="running",
            expected_version=run["state_version"],
            target="waiting_for_trusted_approval",
            current_step_id="commit",
            event_type="trusted_approval_required",
        )
        await self.repository.create_wait(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="commit",
            wait_kind="trusted_approval",
            expected_state_version=waiting["state_version"],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        )
        return waiting

    async def _finish_cleanup_audit(
        self, *, owner_subject: str, run: dict[str, Any]
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run["run_id"])
        }
        review = steps["review"]
        await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="review",
            attempt=1,
            expected_state="waiting",
            expected_version=review["state_version"],
            target="succeeded",
            output_ref=review["output_ref"],
        )
        running = await self.repository.transition_run(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            expected_state="waiting_for_user",
            expected_version=run["state_version"],
            target="running",
            current_step_id="finish",
            event_type="submit_input",
        )
        await self._ready_step(owner_subject, run["run_id"], "finish")
        await self._complete_ready_step(
            owner_subject, run["run_id"], "finish", {"result": {"status": "ok"}}
        )
        return await self.repository.transition_run(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            expected_state="running",
            expected_version=running["state_version"],
            target="succeeded",
            current_step_id=None,
            event_type="completed",
        )

    async def _ready_step(
        self, owner_subject: str, run_id: str, step_id: str
    ) -> dict[str, Any]:
        return await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run_id,
            step_id=step_id,
            attempt=1,
            expected_state="pending",
            expected_version=0,
            target="ready",
        )

    async def _complete_ready_step(
        self,
        owner_subject: str,
        run_id: str,
        step_id: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run_id)
        }
        ready = steps[step_id]
        running = await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run_id,
            step_id=step_id,
            attempt=1,
            expected_state="ready",
            expected_version=ready["state_version"],
            target="running",
        )
        return await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run_id,
            step_id=step_id,
            attempt=1,
            expected_state="running",
            expected_version=running["state_version"],
            target="succeeded",
            output_ref=output,
        )

    async def _device_context(
        self, owner_subject: str, device_id: str | None
    ) -> dict[str, Any]:
        if device_id is None:
            return {
                "capabilities": set(),
                "operation_packs": set(),
                "runtime_release_verified": False,
                "capability_evidence_verified": False,
                "identity_generation": 1,
            }
        if self.device_resolver is None:
            raise WorkflowServiceError("feature_disabled")
        try:
            return await self.device_resolver(owner_subject, device_id)
        except Exception as error:
            raise WorkflowServiceError("not_found") from error

    def _support(self, manifest: Any, status: str, context: dict[str, Any]) -> Any:
        return self.catalog.support_for(
            manifest,
            capabilities=set(context.get("capabilities", ())),
            operation_packs=set(context.get("operation_packs", ())),
            policy_epoch=self.policy_epoch,
            required_policy_epoch=self.policy_epoch,
            publication_status=status,
            workflow_enabled=self.enabled,
            write_enabled=self.write_enabled,
            runtime_release_verified=bool(
                context.get("runtime_release_verified", False)
            ),
            capability_evidence_verified=bool(
                context.get("capability_evidence_verified", False)
            ),
        )

    def _skill_enabled(self, skill_id: str) -> bool:
        return (
            (not self.allowlist or skill_id in self.allowlist)
            and skill_id in self.enabled_skills
        )

    @staticmethod
    def _bounded_id(value: str, field: str) -> None:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise WorkflowServiceError(f"invalid_{field}")

    @staticmethod
    def _run_response(run: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
        return {
            "run_id": run["run_id"],
            "pins": run["pins"],
            "state": run["state"],
            "state_version": run["state_version"],
            "current_step_id": run.get("current_step_id"),
            "required_next_action": _required_next_action(run, None),
            "replayed": replay,
            "resource_uri": f"cad://workflows/{run['run_id']}",
        }


def _required_next_action(
    run: dict[str, Any], wait: dict[str, Any] | None
) -> str | None:
    if wait is not None:
        return {
            "user_input": "submit_input",
            "program_revision": "attach_program_revision",
            "trusted_approval": "approve_in_portal",
            "job": "wait",
            "recovery": "operator_recovery",
        }.get(wait["wait_kind"])
    return {
        "running": "wait",
        "waiting_for_user": "submit_input",
        "waiting_for_program_revision": "attach_program_revision",
        "waiting_for_trusted_approval": "approve_in_portal",
        "waiting_for_job": "wait",
        "waiting_for_recovery": "operator_recovery",
        "paused": "resume",
    }.get(run["state"])


def _validate_schema(schema: dict[str, Any], value: Any, path: str = "inputs") -> None:
    """Validate the bounded JSON-Schema subset accepted by first-party assets."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise WorkflowServiceError("invalid_request")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(key not in value for key in required):
            raise WorkflowServiceError("invalid_request")
        if schema.get("additionalProperties") is False and not set(value) <= set(
            properties
        ):
            raise WorkflowServiceError("invalid_request")
        for key, item in value.items():
            if key in properties:
                _validate_schema(properties[key], item, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise WorkflowServiceError("invalid_request")
        if len(value) < int(schema.get("minItems", 0)) or len(value) > int(
            schema.get("maxItems", 4096)
        ):
            raise WorkflowServiceError("invalid_request")
        for item in value:
            _validate_schema(schema.get("items", {}), item, path)
    elif kind == "string":
        if not isinstance(value, str):
            raise WorkflowServiceError("invalid_request")
        if len(value) < int(schema.get("minLength", 0)) or len(value) > int(
            schema.get("maxLength", 4096)
        ):
            raise WorkflowServiceError("invalid_request")
        if "const" in schema and value != schema["const"]:
            raise WorkflowServiceError("invalid_request")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkflowServiceError("invalid_request")
        _validate_number_bounds(schema, value)
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WorkflowServiceError("invalid_request")
        _validate_number_bounds(schema, value)
    elif kind == "boolean" and not isinstance(value, bool):
        raise WorkflowServiceError("invalid_request")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise WorkflowServiceError("invalid_request") from error
    if len(encoded) > 65_536:
        raise WorkflowServiceError("invalid_request")


def _validate_number_bounds(schema: dict[str, Any], value: int | float) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise WorkflowServiceError("invalid_request")
    if "maximum" in schema and value > schema["maximum"]:
        raise WorkflowServiceError("invalid_request")
