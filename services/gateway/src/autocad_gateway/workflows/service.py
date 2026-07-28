"""Phase 9 application facade.  The FastMCP layer only calls this boundary."""
from __future__ import annotations

import uuid
import json
from typing import Any

from ..infrastructure.sqlite.repositories import RepositoryConflict
from ..skills.catalog import CatalogError, SkillCatalog
from ..skills.catalog_repository import CatalogLifecycleError, SkillCatalogRepository


class WorkflowServiceError(ValueError):
    pass


class WorkflowApplicationService:
    """Owner-scoped catalog/run facade; it never accepts an approval decision."""

    def __init__(self, repository: Any, catalog_repository: SkillCatalogRepository, catalog: SkillCatalog,
                 *, enabled: bool, catalog_enabled: bool, policy_epoch: int, write_enabled: bool) -> None:
        self.repository = repository
        self.catalog_repository = catalog_repository
        self.catalog = catalog
        self.enabled = enabled
        self.catalog_enabled = catalog_enabled
        self.policy_epoch = policy_epoch
        self.write_enabled = write_enabled

    def list_skills(self) -> list[dict[str, Any]]:
        if not self.catalog_enabled:
            return []
        values: list[dict[str, Any]] = []
        for manifest in self.catalog.list():
            try:
                status = self.catalog_repository.get_status(manifest.skill_id, manifest.version)
            except CatalogLifecycleError:
                status = "withdrawn"
            if status in {"withdrawn", "security_revoked"}:
                continue
            support = self.catalog.support_for(
                manifest, capabilities=set(), operation_packs=set(), policy_epoch=self.policy_epoch,
                required_policy_epoch=self.policy_epoch, publication_status=status,
                workflow_enabled=self.enabled, write_enabled=self.write_enabled,
                runtime_release_verified=False, capability_evidence_verified=False,
            )
            values.append({
                "skill_id": manifest.skill_id, "version": manifest.version,
                "title": manifest.title, "summary": manifest.summary, "status": status,
                "support": support.state, "support_reason": support.reason,
                "guide_uri": f"cad://skills/{manifest.skill_id}/versions/{manifest.version}/guide",
            })
        return values

    async def start(self, *, owner_subject: str, actor_subject: str, skill_id: str,
                    version: str | None, device_id: str, inputs: dict[str, Any],
                    idempotency_key: str, scopes: tuple[str, ...] = ()) -> dict[str, Any]:
        if not self.enabled:
            raise WorkflowServiceError("feature_disabled")
        try:
            manifest = self.catalog.resolve(skill_id, version)
            status = self.catalog_repository.get_status(manifest.skill_id, manifest.version)
        except (CatalogError, CatalogLifecycleError) as error:
            raise WorkflowServiceError("not_found") from error
        if status in {"withdrawn", "security_revoked"}:
            raise WorkflowServiceError("not_found")
        if any(scope not in scopes for scope in manifest.required_scopes):
            raise WorkflowServiceError("insufficient_scope")
        if "autocad.write" in manifest.required_scopes and not self.write_enabled:
            raise WorkflowServiceError("feature_disabled")
        # Manifest JSON schema was validated at release import.  Inputs remain bounded
        # durable JSON; wait/schema validation happens before later continuations.
        if not isinstance(inputs, dict) or len(str(inputs).encode()) > 65536:
            raise WorkflowServiceError("invalid_request")
        workflow = manifest.workflow_definition
        pins = {
            "skill_id": manifest.skill_id, "skill_version": manifest.version,
            "skill_digest": manifest.manifest_digest, "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.version, "workflow_digest": workflow.digest,
            "catalog_epoch": self.catalog_repository.get_channel(manifest.skill_id)[1],
            "policy_epoch": self.policy_epoch, "planner_registry_version": "phase9-first-party/1",
            "planner_registry_hash": self.catalog.release_digest,
        }
        try:
            run, replay = await self.repository.create_run(
                owner_subject=owner_subject, actor_issuer="gateway", actor_subject=actor_subject,
                run_id="wfr:" + uuid.uuid4().hex, idempotency_key=idempotency_key, pins=pins,
                inputs=inputs, device_id=device_id, device_identity_generation=1,
            )
            if not replay:
                run = await self.repository.transition_run(
                    owner_subject=owner_subject, run_id=run["run_id"], expected_state="created",
                    expected_version=0, target="running", event_type="started",
                )
            return {"run": run, "replayed": replay, "resource_uri": f"cad://workflows/{run['run_id']}"}
        except RepositoryConflict as error:
            raise WorkflowServiceError(str(error)) from error

    async def get(self, owner_subject: str, run_id: str, *, event_cursor: int = 0) -> dict[str, Any]:
        run = await self.repository.get_run(owner_subject, run_id)
        if run is None:
            raise WorkflowServiceError("not_found")
        return {"run": run, "events": await self.repository.list_events(owner_subject, run_id, cursor=event_cursor, limit=50)}

    async def control(self, *, owner_subject: str, run_id: str, action: str,
                      expected_state: str, expected_state_version: int) -> dict[str, Any]:
        if action not in {"submit_input", "attach_program_revision", "resume", "retry_safe_step", "cancel"}:
            raise WorkflowServiceError("invalid_request")
        try:
            if action == "cancel":
                return await self.repository.cancel_run(owner_subject=owner_subject, run_id=run_id,
                    expected_state=expected_state, expected_version=expected_state_version)
            if action in {"submit_input", "attach_program_revision", "retry_safe_step"}:
                raise WorkflowServiceError("feature_disabled")
            target = "running"
            return await self.repository.transition_run(owner_subject=owner_subject, run_id=run_id,
                expected_state=expected_state, expected_version=expected_state_version, target=target,
                event_type=f"{action}d")
        except RepositoryConflict as error:
            raise WorkflowServiceError(str(error)) from error

    def read_guide(self, skill_id: str, version: str) -> str:
        try:
            manifest = self.catalog.resolve(skill_id, version)
            if self.catalog_repository.get_status(skill_id, version) in {"withdrawn", "security_revoked"}:
                raise CatalogError("skill_not_found")
        except (CatalogError, CatalogLifecycleError) as error:
            raise WorkflowServiceError("not_found") from error
        return json.dumps({
            "skill_id": manifest.skill_id, "version": manifest.version,
            "title": manifest.title, "summary": manifest.summary,
            "guide_digest": manifest.guide_digest,
            "notice": "Guide text is release-owned and has no execution authority.",
        }, sort_keys=True, separators=(",", ":"))
