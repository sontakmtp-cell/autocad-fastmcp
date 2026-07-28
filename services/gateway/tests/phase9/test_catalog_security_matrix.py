from pathlib import Path

import pytest

from autocad_gateway.skills.catalog import CatalogError, SkillCatalog


CATALOG_ROOT = Path(__file__).resolve().parents[4] / "packages" / "skill_catalog"


def test_fixed_catalog_loads_only_sealed_first_party_assets() -> None:
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    skills = list(catalog.list())
    assert {skill.skill_id for skill in skills} == {
        "drawing.cleanup-audit",
        "mechanical.auto-dimension-overall",
        "mechanical.plate-hole-pattern",
    }
    assert all(skill.manifest_digest.startswith("sha256:") for skill in skills)


def test_catalog_support_never_promotes_unverified_or_revoked_skill() -> None:
    catalog = SkillCatalog.from_fixed_package_root(CATALOG_ROOT)
    skill = catalog.resolve("mechanical.plate-hole-pattern")
    unverified = catalog.support_for(
        skill,
        capabilities=set(skill.required_capabilities),
        operation_packs=set(skill.required_operation_packs),
        policy_epoch=0,
        required_policy_epoch=0,
        runtime_release_verified=False,
        capability_evidence_verified=False,
        write_enabled=True,
    )
    revoked = catalog.support_for(
        skill,
        capabilities=set(skill.required_capabilities),
        operation_packs=set(skill.required_operation_packs),
        policy_epoch=0,
        required_policy_epoch=0,
        publication_status="security_revoked",
        runtime_release_verified=True,
        capability_evidence_verified=True,
        write_enabled=True,
    )
    assert (unverified.state, unverified.reason) == ("catalog_only", "runtime_or_capability_evidence_unverified")
    assert (revoked.state, revoked.reason) == ("unsupported", "security_revoked")


def test_fixed_catalog_fails_closed_when_asset_is_changed(tmp_path: Path) -> None:
    copied = tmp_path / "skill_catalog"
    copied.mkdir()
    for source in CATALOG_ROOT.rglob("*"):
        target = copied / source.relative_to(CATALOG_ROOT)
        if source.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.write_bytes(source.read_bytes())
    guide = next(copied.glob("skills/*/1.0.0/guide.md"))
    guide.write_text("tampered", encoding="utf-8")
    with pytest.raises(CatalogError, match="digest mismatch"):
        SkillCatalog.from_fixed_package_root(copied)
