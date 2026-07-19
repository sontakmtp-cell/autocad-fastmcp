import json

import pytest

from autocad_mcp.dimension_profiles import DimensionProfile, DimensionProfileStore


def test_builtin_profiles_capture_metric_and_inch_drafting_rules():
    store = DimensionProfileStore()

    metric = store.get("mechanical_mm")
    inch = store.get("mechanical_inch")

    assert metric.units == "mm"
    assert metric.preferred_layout == "baseline"
    assert metric.quantity_format.format(count=4, value="Ø10") == "4x Ø10"
    assert inch.units == "inch"
    assert inch.precision == 3


def test_custom_profile_is_persisted_and_loaded(tmp_path):
    path = tmp_path / "dimension_profiles.json"
    store = DimensionProfileStore(path)
    profile = store.save(
        {
            **store.get("mechanical_mm").to_dict(),
            "name": "khay_mm",
            "precision": 3,
            "tolerance_mode": "symmetric",
            "tolerance_upper": 0.02,
            "tolerance_lower": 0.02,
        }
    )

    assert profile.name == "khay_mm"
    assert DimensionProfileStore(path).get("khay_mm").tolerance_upper == 0.02
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_profile_overrides_are_temporary_and_validated():
    store = DimensionProfileStore()

    changed = store.get("mechanical_mm", {"precision": 1, "row_spacing": 9})

    assert changed.precision == 1
    assert changed.row_spacing == 9
    assert store.get("mechanical_mm").precision == 2
    with pytest.raises(ValueError, match="precision"):
        store.get("mechanical_mm", {"precision": 99})
    with pytest.raises(ValueError, match="boolean"):
        store.get("mechanical_mm", {"include_centerlines": "sometimes"})


def test_invalid_or_builtin_custom_profiles_are_rejected():
    store = DimensionProfileStore()
    invalid = store.get("mechanical_mm").to_dict()
    invalid["quantity_format"] = "x4"
    with pytest.raises(ValueError, match="quantity_format"):
        DimensionProfile.from_dict(invalid)
    with pytest.raises(ValueError, match="cannot be replaced"):
        store.save(store.get("mechanical_mm").to_dict())
