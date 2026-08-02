from copy import deepcopy
from pathlib import Path

import pytest
from autocad_contracts.phase9_contracts import (
    canonical_skill_manifest_digest,
    canonical_workflow_definition_digest,
)

from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.skills.catalog import SkillCatalog
from autocad_gateway.skills.catalog_repository import (
    CatalogLifecycleError,
    SkillCatalogRepository,
)


CATALOG_ROOT = Path(__file__).resolve().parents[4] / "packages" / "skill_catalog"


@pytest.mark.asyncio
async def test_additive_catalog_release_upgrades_old_database_without_mutating_versions(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "catalog-upgrade.sqlite")
    await database.open()
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    repository = SkillCatalogRepository(database)
    phase9_manifest = catalog.resolve("drawing.cleanup-audit", "1.0.0")
    phase9_workflow = catalog.workflow_for(phase9_manifest)
    repository.import_version(
        phase9_manifest.model_dump(mode="json"),
        phase9_workflow.model_dump(mode="json"),
        release_digest="sha256:" + "0" * 64,
    )

    repository.import_catalog(catalog)

    assert repository.get_status("drawing.cleanup-audit", "1.0.0") == "published"
    assert repository.get_status("drawing.cleanup-audit", "1.1.0") == "published"
    assert repository.get_channel("drawing.cleanup-audit")[0] == "1.0.0"
    assert (
        repository.get_status("mechanical.auto-dimension-overall", "1.1.0")
        == "published"
    )
    assert (
        repository.get_channel("mechanical.auto-dimension-overall")[0]
        == "1.0.0"
    )

    changed_manifest = phase9_manifest.model_dump(mode="json")
    changed_manifest["title"] = "mutated immutable title"
    changed_manifest["manifest_digest"] = canonical_skill_manifest_digest(
        changed_manifest
    )
    with pytest.raises(CatalogLifecycleError, match="immutable_version_conflict"):
        repository.import_version(
            changed_manifest,
            phase9_workflow.model_dump(mode="json"),
            release_digest=catalog.release_digest,
        )

    changed_workflow = deepcopy(phase9_workflow.model_dump(mode="json"))
    changed_workflow["steps"][0]["timeout_seconds"] = 299
    changed_workflow["definition_digest"] = canonical_workflow_definition_digest(
        changed_workflow
    )
    changed_workflow_manifest = phase9_manifest.model_dump(mode="json")
    changed_workflow_manifest["workflow_definition"]["digest"] = changed_workflow[
        "definition_digest"
    ]
    changed_workflow_manifest["manifest_digest"] = canonical_skill_manifest_digest(
        changed_workflow_manifest
    )
    with pytest.raises(CatalogLifecycleError, match="immutable_version_conflict"):
        repository.import_version(
            changed_workflow_manifest,
            changed_workflow,
            release_digest=catalog.release_digest,
        )
    await database.close()
