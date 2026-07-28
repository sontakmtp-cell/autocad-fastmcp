"""In-memory validation and support calculation for bundled skill assets only."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from autocad_contracts import (
    SkillManifest,
    WorkflowDefinition,
    parse_skill_manifest,
    parse_workflow_definition,
)


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class SkillSupport:
    state: str
    reason: str | None = None


@dataclass(frozen=True)
class CatalogSnapshot:
    release_digest: str
    manifests: dict[tuple[str, str], SkillManifest]
    workflows: dict[tuple[str, str], WorkflowDefinition]
    channels: dict[tuple[str, str], str]


class SkillCatalog:
    """A catalog loaded from trusted release bytes, never caller-selected paths."""

    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot

    @classmethod
    def from_release_bundle(cls, bundle: dict[str, Any]) -> "SkillCatalog":
        allowed = {"release_digest", "skills", "workflows", "channels"}
        if set(bundle) != allowed:
            raise CatalogError("catalog release bundle shape is invalid")
        release_digest = bundle["release_digest"]
        if not isinstance(release_digest, str) or not release_digest.startswith("sha256:"):
            raise CatalogError("catalog release digest is invalid")
        try:
            workflows = {
                (item["workflow_id"], item["version"]): parse_workflow_definition(item)
                for item in bundle["workflows"]
            }
            manifests = {
                (item["skill_id"], item["version"]): parse_skill_manifest(item)
                for item in bundle["skills"]
            }
        except (KeyError, TypeError, ValueError) as error:
            raise CatalogError("catalog contains invalid contract") from error
        if len(workflows) != len(bundle["workflows"]) or len(manifests) != len(bundle["skills"]):
            raise CatalogError("catalog contains duplicate version")
        for manifest in manifests.values():
            reference = manifest.workflow_definition
            workflow = workflows.get((reference.workflow_id, reference.version))
            if workflow is None or workflow.definition_digest != reference.digest:
                raise CatalogError("skill workflow reference is not an exact catalog version")
        channels: dict[tuple[str, str], str] = {}
        if not isinstance(bundle["channels"], list):
            raise CatalogError("catalog channels are invalid")
        for channel in bundle["channels"]:
            if set(channel) != {"skill_id", "channel", "default_version", "status"}:
                raise CatalogError("catalog channel shape is invalid")
            key = (channel["skill_id"], channel["channel"])
            if key in channels or channel["channel"] not in {"default", "preview"}:
                raise CatalogError("catalog channel is duplicate or unsupported")
            if channel["status"] != "active":
                continue
            version = channel["default_version"]
            manifest = manifests.get((channel["skill_id"], version))
            if manifest is None:
                raise CatalogError("catalog channel default is missing")
            channels[key] = version
        return cls(CatalogSnapshot(release_digest, manifests, workflows, channels))

    @property
    def release_digest(self) -> str:
        return self._snapshot.release_digest

    def resolve(self, skill_id: str, version: str | None = None, *, channel: str = "default") -> SkillManifest:
        resolved = version or self._snapshot.channels.get((skill_id, channel))
        manifest = self._snapshot.manifests.get((skill_id, resolved or ""))
        if manifest is None:
            raise CatalogError("skill_not_found")
        return manifest

    def list(self) -> Iterable[SkillManifest]:
        return tuple(self._snapshot.manifests[key] for key in sorted(self._snapshot.manifests))

    def support_for(self, manifest: SkillManifest, *, enabled: bool, capabilities: set[str], operation_packs: set[str], policy_epoch: int, required_policy_epoch: int) -> SkillSupport:
        if not enabled:
            return SkillSupport("unsupported", "skill_catalog_disabled")
        if policy_epoch != required_policy_epoch:
            return SkillSupport("unsupported", "policy_epoch_mismatch")
        missing_capabilities = sorted(set(manifest.required_capabilities) - capabilities)
        if missing_capabilities:
            return SkillSupport("unsupported", "missing_capability:" + missing_capabilities[0])
        missing_packs = sorted(set(manifest.required_operation_packs) - operation_packs)
        if missing_packs:
            return SkillSupport("unsupported", "missing_operation_pack:" + missing_packs[0])
        return SkillSupport("supported")
