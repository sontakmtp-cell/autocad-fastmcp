"""In-memory validation and support calculation for bundled skill assets only."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
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
    def from_fixed_package_root(cls, package_root: Path) -> "SkillCatalog":
        """Composition-only loader; callers never supply an asset path per request."""
        root = package_root.resolve()
        manifest_path = root / "catalog.json"
        try:
            bundle = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CatalogError("trusted catalog bundle is unavailable") from error
        assets = bundle.get("assets")
        if not isinstance(assets, dict):
            raise CatalogError("catalog assets are missing")
        for relative, expected in assets.items():
            if not isinstance(relative, str) or not isinstance(expected, str) or "/" not in relative:
                raise CatalogError("catalog asset declaration is invalid")
            candidate = (root / relative).resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise CatalogError("catalog asset is outside fixed package root")
            actual = "sha256:" + sha256(candidate.read_bytes()).hexdigest()
            if actual != expected:
                raise CatalogError("catalog asset digest mismatch")
        release = dict(bundle)
        release.pop("assets", None)
        expected_release = release.pop("release_digest", None)
        actual_release = "sha256:" + sha256(json.dumps(release, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if expected_release != actual_release:
            raise CatalogError("catalog release digest mismatch")
        bundle["release_digest"] = expected_release
        return cls.from_release_bundle(bundle)

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

    def support_for(self, manifest: SkillManifest, *, capabilities: set[str], operation_packs: set[str], policy_epoch: int, required_policy_epoch: int, publication_status: str = "published", owner_access: bool = True, device_access: bool = True, runtime_release_verified: bool = True, capability_evidence_verified: bool = True, planner_available: bool = True, templates_available: bool = True, catalog_enabled: bool = True, workflow_enabled: bool = True, preview_enabled: bool = True, write_enabled: bool = False, certified: bool = False) -> SkillSupport:
        """Return a monotonic support level from Gateway-derived trusted inputs."""
        if publication_status in {"withdrawn", "security_revoked"}:
            return SkillSupport("unsupported", publication_status)
        if not owner_access or not device_access:
            return SkillSupport("unsupported", "not_found")
        if not catalog_enabled:
            return SkillSupport("unsupported", "skill_catalog_disabled")
        if policy_epoch != required_policy_epoch:
            return SkillSupport("unsupported", "policy_epoch_mismatch")
        missing_capabilities = sorted(set(manifest.required_capabilities) - capabilities)
        if missing_capabilities:
            return SkillSupport("unsupported", "missing_capability:" + missing_capabilities[0])
        missing_packs = sorted(set(manifest.required_operation_packs) - operation_packs)
        if missing_packs:
            return SkillSupport("unsupported", "missing_operation_pack:" + missing_packs[0])
        if not runtime_release_verified or not capability_evidence_verified:
            return SkillSupport("catalog_only", "runtime_or_capability_evidence_unverified")
        if not planner_available or not templates_available:
            return SkillSupport("catalog_only", "catalog_component_unavailable")
        if not workflow_enabled:
            return SkillSupport("catalog_only")
        if not preview_enabled:
            return SkillSupport("dry_run")
        if not write_enabled:
            return SkillSupport("preview_only", "write_workflows_disabled")
        if certified:
            return SkillSupport("certified", "deprecated" if publication_status == "deprecated" else None)
        return SkillSupport("lab_commit", "deprecated" if publication_status == "deprecated" else None)
