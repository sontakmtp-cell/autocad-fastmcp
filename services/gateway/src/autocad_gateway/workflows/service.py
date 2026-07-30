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
from .runner import SceneWorkflowPort

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_CONTROL_ACTIONS = {
    "submit_input",
    "resume",
    "cancel",
}
_SCENE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class WorkflowServiceError(ValueError):
    pass


DeviceResolver = Callable[[str, str], Awaitable[dict[str, Any]]]
SnapshotResolver = Callable[[str, str, str], Awaitable[dict[str, Any]]]
WritePreviewExecutor = Callable[
    [str, str, str, str, dict[str, Any], str, tuple[str, ...]],
    Awaitable[dict[str, Any]],
]
CommitRequestExecutor = Callable[
    [str, str, str, tuple[str, ...]], Awaitable[dict[str, Any]]
]
CommitStatusResolver = Callable[
    [str, str], Awaitable[dict[str, Any]]
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
        write_preview_executor: WritePreviewExecutor | None = None,
        commit_request_executor: CommitRequestExecutor | None = None,
        action_runner: Any | None = None,
        commit_status_resolver: CommitStatusResolver | None = None,
        scene_port: SceneWorkflowPort | None = None,
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
        self.write_preview_executor = write_preview_executor
        self.commit_request_executor = commit_request_executor
        self.action_runner = action_runner
        self.commit_status_resolver = commit_status_resolver
        self.scene_port = scene_port
        if scene_port is not None and action_runner is not None:
            setter = getattr(action_runner, "set_scene_port", None)
            if setter is None:
                raise ValueError("action runner does not support scene workflows")
            setter(scene_port)

    def initialize_catalog(self) -> None:
        if self.catalog_enabled:
            self.catalog_repository.import_catalog(self.catalog)

    async def reconcile_restart(self) -> None:
        if self.action_runner is not None:
            await self.action_runner.reconcile_restart()
        for run in await self.repository.list_nonterminal_runs():
            try:
                self._require_continuation_allowed(run)
            except WorkflowServiceError:
                await self.repository.security_revoke_run(
                    owner_subject=run["owner_subject"], run_id=run["run_id"]
                )
                continue
            try:
                await self._resume_run(run)
            except WorkflowServiceError:
                # A temporarily unavailable snapshot/child service must not
                # prevent other durable runs from reconciling at startup.
                continue

    async def maintenance_once(self) -> None:
        if self.action_runner is not None:
            for _ in range(64):
                if not await self.action_runner.run_once():
                    break
        await self.reconcile_restart()

    async def _reconcile_commit_status(self, run: dict[str, Any]) -> None:
        if self.commit_status_resolver is None:
            return
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(
                run["owner_subject"], run["run_id"]
            )
        }
        commit = steps.get("commit")
        intent = ((commit or {}).get("output_ref") or {}).get("result") or {}
        intent_id = intent.get("intent_id")
        if not isinstance(intent_id, str):
            return
        status = await self.commit_status_resolver(
            run["owner_subject"], intent_id
        )
        state = status.get("state")
        if state in {"awaiting_approval", "ready"}:
            current = await self.repository.get_run(
                run["owner_subject"], run["run_id"]
            )
            if current["state"] == "waiting_for_recovery":
                await self.repository.resolve_open_waits_system(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    reason="approval_state_recovered",
                )
                current = await self.repository.transition_run(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    expected_state="waiting_for_recovery",
                    expected_version=current["state_version"],
                    target="waiting_for_trusted_approval",
                    current_step_id="commit",
                    event_type="commit_approval_recovered",
                )
            if current["state"] == "waiting_for_trusted_approval":
                await self.repository.create_wait(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    step_id="commit",
                    wait_kind="trusted_approval",
                    expected_state_version=current["state_version"],
                    response_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                )
            return
        if state in {"queued", "dispatched", "acknowledged", "running"}:
            current = await self.repository.get_run(
                run["owner_subject"], run["run_id"]
            )
            if current["state"] in {
                "waiting_for_trusted_approval",
                "waiting_for_recovery",
            }:
                await self.repository.resolve_open_waits_system(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    reason="approval_released",
                )
                current = await self.repository.transition_run(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    expected_state=current["state"],
                    expected_version=current["state_version"],
                    target="waiting_for_job",
                    current_step_id="commit",
                    event_type="commit_job_released",
                )
            if current["state"] == "waiting_for_job":
                await self.repository.create_wait(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    step_id="commit",
                    wait_kind="job",
                    expected_state_version=current["state_version"],
                    response_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                )
            return
        if state in {"outcome_unknown", "reconnect_pending"}:
            if run["state"] != "waiting_for_recovery":
                await self.repository.resolve_open_waits_system(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    reason="commit_recovery_required",
                )
                await self.repository.transition_run(
                    owner_subject=run["owner_subject"],
                    run_id=run["run_id"],
                    expected_state=run["state"],
                    expected_version=run["state_version"],
                    target="waiting_for_recovery",
                    current_step_id="commit",
                    event_type="commit_recovery_required",
                )
            current = await self.repository.get_run(
                run["owner_subject"], run["run_id"]
            )
            await self.repository.create_wait(
                owner_subject=run["owner_subject"],
                run_id=run["run_id"],
                step_id="commit",
                wait_kind="recovery",
                expected_state_version=current["state_version"],
                response_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            )
            return
        if state != "succeeded":
            await self.repository.mark_run_needs_attention(
                owner_subject=run["owner_subject"],
                run_id=run["run_id"],
                reason=f"commit_{state or 'status_missing'}",
            )
            return
        await self.repository.resolve_open_waits_system(
            owner_subject=run["owner_subject"],
            run_id=run["run_id"],
            reason="commit_succeeded",
        )
        if commit["state"] == "waiting":
            commit = await self.repository.transition_step(
                owner_subject=run["owner_subject"],
                run_id=run["run_id"],
                step_id="commit",
                attempt=1,
                expected_state="waiting",
                expected_version=commit["state_version"],
                target="succeeded",
                output_ref={"result": status},
            )
        await self._finish_write_after_commit(run, status)

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
                default_version, _ = self.catalog_repository.get_channel(
                    manifest.skill_id
                )
                if manifest.version != default_version:
                    continue
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
                    "default_version": default_version,
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
            "actor_scopes": sorted(set(scopes)),
        }
        try:
            materialized_steps = [
                {
                    "step_id": step.step_id,
                    "attempt": 1,
                    "kind": step.kind,
                    "input_ref": {
                        key: (
                            value.model_dump(mode="json")
                            if hasattr(value, "model_dump")
                            else value
                        )
                        for key, value in step.input_bindings.items()
                    },
                }
                for step in workflow.steps
            ]
            first = sorted(
                (step for step in workflow.steps if not step.depends_on),
                key=lambda step: step.step_id,
            )[0]
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
                steps=materialized_steps,
                first_step_id=first.step_id,
            )
            run = await self._resume_run(
                run,
                manifest=manifest,
                snapshot=snapshot,
                scopes=scopes,
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
        if run["state"] == "waiting_for_user":
            await self._heal_user_input_wait(run)
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
        scopes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if action not in _CONTROL_ACTIONS:
            raise WorkflowServiceError("invalid_request")
        self._bounded_id(idempotency_key, "idempotency_key")
        run = await self.repository.get_run(owner_subject, run_id)
        if run is None:
            raise WorkflowServiceError("not_found")
        try:
            manifest = self.catalog.resolve(
                run["skill_id"], run["skill_version"]
            )
            continuation_status = self.catalog_repository.get_status(
                run["skill_id"], run["skill_version"]
            )
        except (CatalogError, CatalogLifecycleError) as error:
            raise WorkflowServiceError("not_found") from error
        if continuation_status in {"security_revoked", "withdrawn"}:
            await self.repository.security_revoke_run(
                owner_subject=owner_subject, run_id=run_id
            )
            raise WorkflowServiceError(
                "skill_security_revoked"
                if continuation_status == "security_revoked"
                else "skill_withdrawn"
            )
        if (
            "autocad.write" in manifest.required_scopes
            and action != "cancel"
            and "autocad.write" not in scopes
        ):
            raise WorkflowServiceError("insufficient_scope")
        if action == "submit_input" and run["state"] == "waiting_for_user":
            await self._heal_user_input_wait(run)
        try:
            command, replay = await self.repository.begin_control_command(
                owner_subject=owner_subject,
                run_id=run_id,
                action=action,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
                payload=payload or {},
            )
            if replay and command["state"] == "completed":
                return command["result"]

            async def finish(value: dict[str, Any]) -> dict[str, Any]:
                await self.repository.complete_control_command(
                    owner_subject=owner_subject,
                    idempotency_key=idempotency_key,
                    result=value,
                )
                return value

            if (
                replay
                and action != "submit_input"
                and run["state_version"] != expected_state_version
            ):
                return await finish(self._run_response(run))
            if action == "cancel":
                result = await self.repository.cancel_run(
                    owner_subject=owner_subject,
                    run_id=run_id,
                    expected_state=run["state"],
                    expected_version=expected_state_version,
                )
                return await finish(self._run_response(result))
            if action == "submit_input":
                wait = await self.repository.current_wait(owner_subject, run_id)
                if wait is None and replay:
                    wait = await self.repository.wait_resolved_by_command(
                        owner_subject, run_id, idempotency_key
                    )
                if wait is None or wait["wait_kind"] != "user_input":
                    raise WorkflowServiceError("invalid_workflow_state")
                response = payload or {}
                _validate_schema(wait["response_schema"], response)
                if wait.get("resolution") is None:
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
                    return await finish(self._run_response(result))
                if run["skill_id"] == "mechanical.auto-dimension-overall":
                    run = await self.repository.get_run(owner_subject, run_id)
                    result = await self._request_auto_dimension_commit(
                        owner_subject=owner_subject,
                        run=run,
                        idempotency_key=idempotency_key,
                        scopes=scopes,
                    )
                    return await finish(self._run_response(result))
            elif action == "resume" and run["state"] != "paused":
                raise WorkflowServiceError("invalid_workflow_state")
            result = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run_id,
                expected_state=run["state"],
                expected_version=expected_state_version,
                target="running",
                current_step_id=run.get("current_step_id"),
                event_type=action,
            )
            return await finish(self._run_response(result))
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

    async def _resume_run(
        self,
        run: dict[str, Any],
        *,
        manifest: Any | None = None,
        snapshot: dict[str, Any] | None = None,
        scopes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if run["state"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            return run
        manifest = manifest or self._published_manifest(
            run["skill_id"], run["skill_version"]
        )
        if manifest.skill_id == "drawing.cleanup-audit":
            if run["state"] not in {"running", "waiting_for_user"}:
                return run
            if manifest.version != "1.0.0":
                return await self._advance_scene_cleanup_audit(
                    owner_subject=run["owner_subject"],
                    run=run,
                    inputs=run["inputs"],
                )
            if snapshot is None:
                if self.snapshot_resolver is None or not run["initial_snapshot_id"]:
                    raise WorkflowServiceError("feature_disabled")
                snapshot = await self.snapshot_resolver(
                    run["owner_subject"],
                    run["device_id"],
                    run["initial_snapshot_id"],
                )
            return await self._advance_cleanup_audit(
                owner_subject=run["owner_subject"],
                run=run,
                snapshot=snapshot,
                inputs=run["inputs"],
            )
        if "autocad.write" not in manifest.required_scopes:
            return run
        actions = await self.repository.list_actions(
            run["owner_subject"], run["run_id"]
        )
        durable_scopes = tuple(
            scopes
            or next(
                (
                    tuple(action["payload"].get("scopes", ()))
                    for action in actions
                    if action["payload"].get("scopes")
                ),
                tuple(run["pins"].get("actor_scopes", ())),
            )
        )
        if "autocad.write" not in durable_scopes:
            return await self.repository.mark_run_needs_attention(
                owner_subject=run["owner_subject"],
                run_id=run["run_id"],
                reason="write_scope_evidence_missing",
            )
        run = await self._resume_write_run(
            run,
            manifest=manifest,
            snapshot=snapshot or {},
            scopes=durable_scopes,
        )
        command = await self.repository.started_control_command(
            run["owner_subject"], run["run_id"], "submit_input"
        )
        resolved = (
            await self.repository.wait_resolved_by_command(
                run["owner_subject"], run["run_id"], command["idempotency_key"]
            )
            if command is not None
            else None
        )
        if command is not None and resolved is not None and run["state"] != "running":
            await self.repository.complete_control_command(
                owner_subject=run["owner_subject"],
                idempotency_key=command["idempotency_key"],
                result=self._run_response(run),
            )
        return run

    async def _resume_write_run(
        self,
        run: dict[str, Any],
        *,
        manifest: Any,
        snapshot: dict[str, Any],
        scopes: tuple[str, ...],
    ) -> dict[str, Any]:
        owner_subject = run["owner_subject"]
        run_id = run["run_id"]
        reconcile_existing_wait = run["state"] in {
            "waiting_for_trusted_approval",
            "waiting_for_job",
            "waiting_for_recovery",
        }
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run_id)
        }
        commit = steps["commit"]
        if commit["state"] == "succeeded":
            status = (commit.get("output_ref") or {}).get("result") or {}
            return await self._finish_write_after_commit(run, status)

        command = await self.repository.started_control_command(
            owner_subject, run_id, "submit_input"
        )
        commit_key = (
            command["idempotency_key"]
            if command is not None
            else f"{run['idempotency_key']}:commit"
        )
        if manifest.skill_id == "mechanical.auto-dimension-overall":
            review = steps["review"]
            if review["state"] == "waiting" and run["state"] == "waiting_for_user":
                resolved = (
                    await self.repository.wait_resolved_by_command(
                        owner_subject, run_id, command["idempotency_key"]
                    )
                    if command is not None
                    else None
                )
                if resolved is None:
                    await self._heal_user_input_wait(run)
                    return await self.repository.get_run(owner_subject, run_id)
            if review["state"] in {"waiting", "succeeded"} and run["state"] in {
                "waiting_for_user",
                "running",
            }:
                run = await self._request_auto_dimension_commit(
                    owner_subject=owner_subject,
                    run=run,
                    idempotency_key=commit_key,
                    scopes=scopes,
                )
            elif run["state"] == "running":
                run = await self._advance_write_preview(
                    owner_subject=owner_subject,
                    run=run,
                    manifest=manifest,
                    snapshot=snapshot,
                    inputs=run["inputs"],
                    idempotency_key=run["idempotency_key"],
                    scopes=scopes,
                )
        elif run["state"] == "running":
            preview = steps["preview"]
            preview_result = (preview.get("output_ref") or {}).get("result") or {}
            if commit["state"] in {"ready", "running", "waiting"} and isinstance(
                preview_result.get("preview_id"), str
            ):
                run = await self._request_commit(
                    owner_subject=owner_subject,
                    run=run,
                    preview_id=preview_result["preview_id"],
                    idempotency_key=commit_key,
                    scopes=scopes,
                )
            else:
                run = await self._advance_write_preview(
                    owner_subject=owner_subject,
                    run=run,
                    manifest=manifest,
                    snapshot=snapshot,
                    inputs=run["inputs"],
                    idempotency_key=run["idempotency_key"],
                    scopes=scopes,
                )

        run = await self.repository.get_run(owner_subject, run_id)
        actions = await self.repository.list_actions(owner_subject, run_id)
        completed_commit = next(
            (
                action
                for action in actions
                if action["action_kind"] == "commit"
                and action["state"] == "completed"
            ),
            None,
        )
        if run["state"] == "waiting_for_recovery" and completed_commit is not None:
            payload = completed_commit["payload"]
            run = await self._request_commit(
                owner_subject=owner_subject,
                run=run,
                preview_id=str(payload["preview_id"]),
                idempotency_key=completed_commit["idempotency_key"],
                scopes=tuple(payload["scopes"]),
            )
        if reconcile_existing_wait and run["state"] in {
            "waiting_for_trusted_approval",
            "waiting_for_job",
            "waiting_for_recovery",
        }:
            await self._reconcile_commit_status(run)
        return await self.repository.get_run(owner_subject, run_id)

    async def _heal_user_input_wait(self, run: dict[str, Any]) -> None:
        if run["state"] != "waiting_for_user":
            return
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(
                run["owner_subject"], run["run_id"]
            )
        }
        review = steps.get("review")
        if review is None or review["state"] != "waiting":
            return
        await self.repository.create_wait(
            owner_subject=run["owner_subject"],
            run_id=run["run_id"],
            step_id="review",
            wait_kind="user_input",
            expected_state_version=run["state_version"],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "const": "continue"}
                },
            },
        )

    async def _finish_write_after_commit(
        self, run: dict[str, Any], status: dict[str, Any]
    ) -> dict[str, Any]:
        owner_subject = run["owner_subject"]
        run_id = run["run_id"]
        current = await self.repository.get_run(owner_subject, run_id)
        if current["state"] in {
            "waiting_for_trusted_approval",
            "waiting_for_job",
            "waiting_for_recovery",
        }:
            await self.repository.resolve_open_waits_system(
                owner_subject=owner_subject,
                run_id=run_id,
                reason="commit_succeeded",
            )
            current = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run_id,
                expected_state=current["state"],
                expected_version=current["state_version"],
                target="running",
                current_step_id="validate",
                event_type="commit_succeeded",
            )
        if current["state"] != "running":
            return current
        for step_id in ("job", "validate", "finish"):
            await self._complete_step_idempotently(
                owner_subject,
                run_id,
                step_id,
                {"result": status},
            )
        current = await self.repository.get_run(owner_subject, run_id)
        if current["state"] == "running":
            current = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run_id,
                expected_state="running",
                expected_version=current["state_version"],
                target="succeeded",
                current_step_id="finish",
                event_type="workflow_succeeded",
            )
        return current

    async def _advance_scene_cleanup_audit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        if self.scene_port is None or self.action_runner is None:
            raise WorkflowServiceError("feature_disabled")
        if inputs.get("document_revision") != run["initial_document_revision"]:
            raise WorkflowServiceError("binding_mismatch")
        run_id = run["run_id"]
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run_id)
        }
        if steps["review"]["state"] == "succeeded":
            current = await self.repository.get_run(owner_subject, run_id)
            if current["state"] == "waiting_for_user":
                current = await self.repository.transition_run(
                    owner_subject=owner_subject,
                    run_id=run_id,
                    expected_state="waiting_for_user",
                    expected_version=current["state_version"],
                    target="running",
                    current_step_id="finish",
                    event_type="scene_cleanup_review_recovered",
                )
            if current["state"] == "running":
                await self._complete_step_idempotently(
                    owner_subject,
                    run_id,
                    "finish",
                    {"result": {"status": "ok", "write_authority": False}},
                )
                current = await self.repository.get_run(owner_subject, run_id)
                return await self.repository.transition_run(
                    owner_subject=owner_subject,
                    run_id=run_id,
                    expected_state="running",
                    expected_version=current["state_version"],
                    target="succeeded",
                    current_step_id=None,
                    event_type="completed",
                )
            return current

        build = (steps["build_scene"].get("output_ref") or {}).get("result")
        if build is None:
            source_digest = await self.scene_port.source_digest(
                owner_subject=owner_subject,
                device_id=run["device_id"],
                source_snapshot_id=run["initial_snapshot_id"],
                document_revision=run["initial_document_revision"],
                analysis_profile="mechanical-2d/1",
            )
            if not isinstance(source_digest, str) or not _SCENE_DIGEST.fullmatch(
                source_digest
            ):
                raise WorkflowServiceError("scene_source_digest_invalid")
            build = await self._run_scene_action(
                owner_subject=owner_subject,
                run=run,
                step_id="build_scene",
                action_kind="build_scene",
                source_digest=source_digest,
                payload={
                    "owner_subject": owner_subject,
                    "skill_id": run["skill_id"],
                    "skill_version": run["skill_version"],
                    "device_id": run["device_id"],
                    "source_snapshot_id": run["initial_snapshot_id"],
                    "document_revision": run["initial_document_revision"],
                    "analysis_profile": "mechanical-2d/1",
                    "space": "model",
                    "include_sections": [
                        "nodes",
                        "issues",
                        "evidence",
                    ],
                    "source_digest": source_digest,
                },
            )
            if build is None:
                return await self.repository.get_run(owner_subject, run_id)

        source_digest = str(build.get("source_digest", ""))
        scene_id = str(build.get("scene_id", ""))
        scene_digest = str(build.get("scene_digest", ""))
        self._validate_scene_binding(
            build,
            source_digest=source_digest,
            source_snapshot_id=run["initial_snapshot_id"],
            document_revision=run["initial_document_revision"],
        )

        query = (steps["query_scene"].get("output_ref") or {}).get("result")
        if query is None:
            query = await self._run_scene_action(
                owner_subject=owner_subject,
                run=run,
                step_id="query_scene",
                action_kind="query_scene",
                source_digest=source_digest,
                payload={
                    "owner_subject": owner_subject,
                    "skill_id": run["skill_id"],
                    "skill_version": run["skill_version"],
                    "scene_id": scene_id,
                    "scene_digest": scene_digest,
                    "source_digest": source_digest,
                    "source_snapshot_id": run["initial_snapshot_id"],
                    "document_revision": run["initial_document_revision"],
                    "section": "issues",
                    "limit": min(int(inputs["max_candidates"]), 128),
                },
            )
            if query is None:
                return await self.repository.get_run(owner_subject, run_id)
        self._validate_scene_binding(
            query,
            source_digest=source_digest,
            scene_id=scene_id,
            scene_digest=scene_digest,
        )

        validation = (steps["validate_scene"].get("output_ref") or {}).get(
            "result"
        )
        if validation is None:
            validation = await self._run_scene_action(
                owner_subject=owner_subject,
                run=run,
                step_id="validate_scene",
                action_kind="validate_scene",
                source_digest=source_digest,
                payload={
                    "owner_subject": owner_subject,
                    "skill_id": run["skill_id"],
                    "skill_version": run["skill_version"],
                    "scene_id": scene_id,
                    "scene_digest": scene_digest,
                    "source_digest": source_digest,
                    "source_snapshot_id": run["initial_snapshot_id"],
                    "document_revision": run["initial_document_revision"],
                    "validation_profile": "cleanup-audit/1",
                },
            )
            if validation is None:
                return await self.repository.get_run(owner_subject, run_id)
        self._validate_scene_binding(
            validation,
            source_digest=source_digest,
            scene_id=scene_id,
            scene_digest=scene_digest,
            source_snapshot_id=run["initial_snapshot_id"],
            document_revision=run["initial_document_revision"],
        )
        if not isinstance(validation.get("valid"), bool):
            raise WorkflowServiceError("scene_result_invalid")

        items = query.get("items", [])
        if not isinstance(items, list) or len(items) > 128:
            raise WorkflowServiceError("scene_result_invalid")
        issue_codes = sorted(
            {
                str(item["code"])
                for item in items
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            }
        )
        report = {
            "status": "issues_found" if items else "ok",
            "scene_id": scene_id,
            "scene_digest": scene_digest,
            "source_digest": source_digest,
            "source_snapshot_id": run["initial_snapshot_id"],
            "document_revision": run["initial_document_revision"],
            "issue_count": len(items),
            "issue_codes": issue_codes,
            "validation_ok": validation.get("valid") is True,
            "write_authority": False,
        }
        await self._complete_step_idempotently(
            owner_subject, run_id, "report", {"result": report}
        )
        await self._complete_step_idempotently(
            owner_subject,
            run_id,
            "review",
            {"result": report},
            target="waiting",
        )
        current = await self.repository.get_run(owner_subject, run_id)
        if current["state"] == "running":
            current = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run_id,
                expected_state="running",
                expected_version=current["state_version"],
                target="waiting_for_user",
                current_step_id="review",
                event_type="scene_cleanup_report_ready",
            )
        if current["state"] == "waiting_for_user":
            await self.repository.create_wait(
                owner_subject=owner_subject,
                run_id=run_id,
                step_id="review",
                wait_kind="user_input",
                expected_state_version=current["state_version"],
                response_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["decision"],
                    "properties": {
                        "decision": {"type": "string", "const": "continue"}
                    },
                },
            )
        return current

    async def _run_scene_action(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        step_id: str,
        action_kind: str,
        source_digest: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        await self.repository.insert_action(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id=step_id,
            attempt=1,
            action_kind=action_kind,
            payload=payload,
            retry_class="read",
            effect_class="read",
            source_digest=source_digest,
        )
        await self.action_runner.run_once()
        action = next(
            item
            for item in await self.repository.list_actions(
                owner_subject, run["run_id"]
            )
            if item["action_kind"] == action_kind
        )
        if action["state"] == "failed":
            await self.repository.mark_run_needs_attention(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                reason=(
                    f"{action_kind}_failed:"
                    f"{action.get('error_code') or 'backend_error'}"
                ),
            )
            return None
        if action["state"] != "completed" or not isinstance(
            action.get("result"), dict
        ):
            return None
        result = action["result"]
        self._validate_scene_binding(result, source_digest=source_digest)
        await self._complete_step_idempotently(
            owner_subject, run["run_id"], step_id, {"result": result}
        )
        return result

    @staticmethod
    def _validate_scene_binding(
        value: dict[str, Any],
        *,
        source_digest: str,
        scene_id: str | None = None,
        scene_digest: str | None = None,
        source_snapshot_id: str | None = None,
        document_revision: str | None = None,
    ) -> None:
        expected = {
            "source_digest": source_digest,
            "scene_id": scene_id,
            "scene_digest": scene_digest,
            "source_snapshot_id": source_snapshot_id,
            "document_revision": document_revision,
        }
        if not _SCENE_DIGEST.fullmatch(source_digest):
            raise WorkflowServiceError("scene_result_invalid")
        for key, required in expected.items():
            if required is not None and value.get(key) != required:
                raise WorkflowServiceError("scene_binding_mismatch")
        if scene_id is None and (
            not isinstance(value.get("scene_id"), str)
            or not value["scene_id"].startswith("scn_")
            or not isinstance(value.get("scene_digest"), str)
            or not _SCENE_DIGEST.fullmatch(value["scene_digest"])
        ):
            raise WorkflowServiceError("scene_result_invalid")

    async def _advance_cleanup_audit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        snapshot: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(
                owner_subject, run["run_id"]
            )
        }
        if steps["review"]["state"] == "succeeded":
            current = await self.repository.get_run(owner_subject, run["run_id"])
            if current["state"] == "waiting_for_user":
                current = await self.repository.transition_run(
                    owner_subject=owner_subject,
                    run_id=run["run_id"],
                    expected_state="waiting_for_user",
                    expected_version=current["state_version"],
                    target="running",
                    current_step_id="finish",
                    event_type="cleanup_review_recovered",
                )
            if current["state"] == "running":
                await self._complete_step_idempotently(
                    owner_subject,
                    run["run_id"],
                    "finish",
                    {"result": {"status": "ok"}},
                )
                current = await self.repository.get_run(
                    owner_subject, run["run_id"]
                )
                return await self.repository.transition_run(
                    owner_subject=owner_subject,
                    run_id=run["run_id"],
                    expected_state="running",
                    expected_version=current["state_version"],
                    target="succeeded",
                    current_step_id=None,
                    event_type="completed",
                )
            return current

        query_output = (steps["query"].get("output_ref") or {}).get("result")
        if query_output is None:
            entities = snapshot.get("entities")
            if not isinstance(entities, list):
                raise WorkflowServiceError("stale_snapshot")
            layer = inputs["layer"]
            query_output = [
                entity
                for entity in entities
                if not layer or entity.get("layer") == layer
            ][: inputs["page_size"]]
        await self._complete_step_idempotently(
            owner_subject,
            run["run_id"],
            "query",
            {"result": query_output},
        )
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(
                owner_subject, run["run_id"]
            )
        }
        report = (steps["pure"].get("output_ref") or {}).get("result")
        if report is None:
            report = audit_cleanup(
                {
                    "source_snapshot_id": run["initial_snapshot_id"],
                    "document_revision": run["initial_document_revision"],
                },
                query_output,
                max_candidates=inputs["max_candidates"],
            )
        await self._complete_step_idempotently(
            owner_subject, run["run_id"], "pure", {"result": report}
        )
        await self._complete_step_idempotently(
            owner_subject, run["run_id"], "report", {"result": report}
        )
        review = await self._complete_step_idempotently(
            owner_subject,
            run["run_id"],
            "review",
            {"result": report},
            target="waiting",
        )
        current = await self.repository.get_run(owner_subject, run["run_id"])
        if current["state"] == "running":
            current = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                expected_state="running",
                expected_version=current["state_version"],
                target="waiting_for_user",
                current_step_id="review",
                event_type="cleanup_report_ready",
            )
        if current["state"] != "waiting_for_user":
            return current
        del review
        await self.repository.create_wait(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="review",
            wait_kind="user_input",
            expected_state_version=current["state_version"],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "const": "continue"}
                },
            },
        )
        return current

    async def _advance_write_preview(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        manifest: Any,
        snapshot: dict[str, Any],
        inputs: dict[str, Any],
        idempotency_key: str,
        scopes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if self.write_preview_executor is None:
            raise WorkflowServiceError("feature_disabled")
        try:
            if self.action_runner is None:
                output = await self.write_preview_executor(
                    owner_subject,
                    manifest.skill_id,
                    run["device_id"],
                    run["initial_snapshot_id"],
                    inputs,
                    idempotency_key,
                    scopes,
                )
            else:
                await self.repository.insert_action(
                    owner_subject=owner_subject,
                    run_id=run["run_id"],
                    step_id="preview",
                    attempt=1,
                    action_kind="preview",
                    payload={
                        "owner_subject": owner_subject,
                        "skill_id": manifest.skill_id,
                        "skill_version": manifest.version,
                        "device_id": run["device_id"],
                        "snapshot_id": run["initial_snapshot_id"],
                        "inputs": inputs,
                        "scopes": list(scopes),
                    },
                    retry_class="read",
                    effect_class="read",
                )
                await self.action_runner.run_once()
                action = next(
                    item
                    for item in await self.repository.list_actions(
                        owner_subject, run["run_id"]
                    )
                    if item["action_kind"] == "preview"
                )
                if action["state"] != "completed" or not isinstance(
                    action.get("result"), dict
                ):
                    if action["state"] == "failed":
                        return await self.repository.mark_run_needs_attention(
                            owner_subject=owner_subject,
                            run_id=run["run_id"],
                            reason=(
                                "preview_action_failed:"
                                f"{action.get('error_code') or 'backend_error'}"
                            ),
                        )
                    if action.get("error_code") in {
                        "skill_security_revoked",
                        "skill_withdrawn",
                    }:
                        return await self.repository.security_revoke_run(
                            owner_subject=owner_subject, run_id=run["run_id"]
                        )
                    return await self.repository.get_run(
                        owner_subject, run["run_id"]
                    )
                output = action["result"]
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
            await self._complete_step_idempotently(
                owner_subject,
                run["run_id"],
                step_id,
                {"result": output.get(step_id, {})},
            )
        if manifest.skill_id == "mechanical.auto-dimension-overall":
            await self._complete_step_idempotently(
                owner_subject,
                run["run_id"],
                "review",
                output_ref={"result": output["preview"]},
                target="waiting",
            )
            waiting = await self.repository.get_run(owner_subject, run["run_id"])
            if waiting["state"] == "running":
                waiting = await self.repository.transition_run(
                    owner_subject=owner_subject,
                    run_id=run["run_id"],
                    expected_state="running",
                    expected_version=waiting["state_version"],
                    target="waiting_for_user",
                    current_step_id="review",
                    event_type="preview_ready",
                )
            await self._heal_user_input_wait(waiting)
            return waiting
        return await self._request_commit(
            owner_subject=owner_subject,
            run=run,
            preview_id=str(output["preview"]["preview_id"]),
            idempotency_key=f"{idempotency_key}:commit",
            scopes=scopes,
        )

    async def _request_auto_dimension_commit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        idempotency_key: str,
        scopes: tuple[str, ...],
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run["run_id"])
        }
        review = steps["review"]
        preview = (review.get("output_ref") or {}).get("result") or {}
        if not isinstance(preview.get("preview_id"), str):
            raise WorkflowServiceError("binding_mismatch")
        if review["state"] == "waiting":
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
        elif review["state"] != "succeeded":
            raise WorkflowServiceError("invalid_workflow_state")
        running = await self.repository.get_run(owner_subject, run["run_id"])
        if running["state"] == "waiting_for_user":
            running = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                expected_state="waiting_for_user",
                expected_version=running["state_version"],
                target="running",
                current_step_id="commit",
                event_type="submit_input",
            )
        if running["state"] != "running":
            return running
        return await self._request_commit(
            owner_subject=owner_subject,
            run=running,
            preview_id=preview["preview_id"],
            idempotency_key=idempotency_key,
            scopes=scopes,
        )

    async def _request_commit(
        self,
        *,
        owner_subject: str,
        run: dict[str, Any],
        preview_id: str,
        idempotency_key: str,
        scopes: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if self.commit_request_executor is None:
            raise WorkflowServiceError("feature_disabled")
        self._require_continuation_allowed(run)
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(
                owner_subject, run["run_id"]
            )
        }
        commit_step = steps["commit"]
        if commit_step["state"] == "pending":
            await self._ready_step(owner_subject, run["run_id"], "commit")
            commit_step = {
                step["step_id"]: step
                for step in await self.repository.list_steps(
                    owner_subject, run["run_id"]
                )
            }["commit"]
        if commit_step["state"] == "ready":
            commit_step = await self.repository.transition_step(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                step_id="commit",
                attempt=1,
                expected_state="ready",
                expected_version=commit_step["state_version"],
                target="running",
            )
        if commit_step["state"] == "waiting":
            intent = (commit_step.get("output_ref") or {}).get("result")
            if not isinstance(intent, dict):
                raise WorkflowServiceError("binding_mismatch")
        elif commit_step["state"] != "running":
            raise WorkflowServiceError("invalid_workflow_state")
        else:
            try:
                if self.action_runner is None:
                    intent = await self.commit_request_executor(
                        owner_subject, preview_id, idempotency_key, scopes
                    )
                else:
                    await self.repository.insert_action(
                        owner_subject=owner_subject,
                        run_id=run["run_id"],
                        step_id="commit",
                        attempt=1,
                        action_kind="commit",
                        payload={
                            "owner_subject": owner_subject,
                            "skill_id": run["skill_id"],
                            "skill_version": run["skill_version"],
                            "preview_id": preview_id,
                            "scopes": list(scopes),
                        },
                        retry_class="not_started",
                        effect_class="write",
                    )
                    await self.action_runner.run_once()
                    action = next(
                        item
                        for item in await self.repository.list_actions(
                            owner_subject, run["run_id"]
                        )
                        if item["action_kind"] == "commit"
                    )
                    if action["state"] != "completed" or not isinstance(
                        action.get("result"), dict
                    ):
                        return await self.repository.get_run(
                            owner_subject, run["run_id"]
                        )
                    intent = action["result"]
            except Exception as error:
                raise WorkflowServiceError(
                    getattr(error, "code", "backend_error")
                ) from error
            commit_step = await self.repository.transition_step(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                step_id="commit",
                attempt=1,
                expected_state="running",
                expected_version=commit_step["state_version"],
                target="waiting",
                output_ref={"result": intent},
            )
        current = await self.repository.get_run(owner_subject, run["run_id"])
        if current["state"] == "waiting_for_recovery":
            await self._reconcile_commit_status(current)
            return await self.repository.get_run(owner_subject, run["run_id"])
        if current["state"] == "running":
            current = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                expected_state="running",
                expected_version=current["state_version"],
                target="waiting_for_trusted_approval",
                current_step_id="commit",
                event_type="trusted_approval_required",
            )
        if current["state"] != "waiting_for_trusted_approval":
            return current
        await self.repository.create_wait(
            owner_subject=owner_subject,
            run_id=run["run_id"],
            step_id="commit",
            wait_kind="trusted_approval",
            expected_state_version=current["state_version"],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        )
        return current

    def _require_continuation_allowed(self, run: dict[str, Any]) -> None:
        try:
            status = self.catalog_repository.get_status(
                run["skill_id"], run["skill_version"]
            )
        except CatalogLifecycleError as error:
            raise WorkflowServiceError("not_found") from error
        if status == "security_revoked":
            raise WorkflowServiceError("skill_security_revoked")
        if status == "withdrawn":
            raise WorkflowServiceError("skill_withdrawn")

    async def _finish_cleanup_audit(
        self, *, owner_subject: str, run: dict[str, Any]
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run["run_id"])
        }
        review = steps["review"]
        if review["state"] == "waiting":
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
        elif review["state"] != "succeeded":
            raise WorkflowServiceError("invalid_workflow_state")
        running = await self.repository.get_run(owner_subject, run["run_id"])
        if running["state"] == "waiting_for_user":
            running = await self.repository.transition_run(
                owner_subject=owner_subject,
                run_id=run["run_id"],
                expected_state="waiting_for_user",
                expected_version=running["state_version"],
                target="running",
                current_step_id="finish",
                event_type="submit_input",
            )
        if running["state"] != "running":
            return running
        await self._complete_step_idempotently(
            owner_subject,
            run["run_id"],
            "finish",
            {"result": {"status": "ok"}},
        )
        running = await self.repository.get_run(owner_subject, run["run_id"])
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

    async def _complete_step_idempotently(
        self,
        owner_subject: str,
        run_id: str,
        step_id: str,
        output_ref: dict[str, Any],
        *,
        target: str = "succeeded",
    ) -> dict[str, Any]:
        steps = {
            step["step_id"]: step
            for step in await self.repository.list_steps(owner_subject, run_id)
        }
        step = steps[step_id]
        if step["state"] == target:
            return step
        if step["state"] == "pending":
            step = await self._ready_step(owner_subject, run_id, step_id)
        if step["state"] == "ready":
            step = await self.repository.transition_step(
                owner_subject=owner_subject,
                run_id=run_id,
                step_id=step_id,
                attempt=1,
                expected_state="ready",
                expected_version=step["state_version"],
                target="running",
            )
        if step["state"] != "running":
            raise WorkflowServiceError("invalid_workflow_state")
        return await self.repository.transition_step(
            owner_subject=owner_subject,
            run_id=run_id,
            step_id=step_id,
            attempt=1,
            expected_state="running",
            expected_version=step["state_version"],
            target=target,
            output_ref=output_ref,
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
    if run["state"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
        return None
    if wait is not None:
        return {
            "user_input": "submit_input",
            "trusted_approval": "approve_in_portal",
            "job": "wait",
            "recovery": "operator_recovery",
        }.get(wait["wait_kind"])
    return {
        "running": "wait",
        "waiting_for_user": "submit_input",
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
