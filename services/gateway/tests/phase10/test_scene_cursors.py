import pytest

from autocad_gateway.scenes.cursors import decode_cursor, encode_cursor


SECRET = b"phase10-cursor-secret-that-is-at-least-32-bytes"
FILTERS = {"feature_types": ["hole"], "confidence_min": 0.5}


def _decode(cursor: str, **overrides) -> int:
    values = {
        "secret": SECRET,
        "owner_subject": "alice",
        "scene_id": "scn-a",
        "section": "features",
        "filters": FILTERS,
        "projection_version": "cad.entity-projection/2",
    }
    values.update(overrides)
    return decode_cursor(cursor, **values)


def test_signed_cursor_binds_every_public_dimension():
    cursor = encode_cursor(
        secret=SECRET,
        owner_subject="alice",
        scene_id="scn-a",
        section="features",
        filters=FILTERS,
        offset=100,
        projection_version="cad.entity-projection/2",
    )
    assert _decode(cursor) == 100
    for override in (
        {"owner_subject": "bob"},
        {"scene_id": "scn-b"},
        {"section": "issues"},
        {"filters": {"feature_types": ["slot"], "confidence_min": 0.5}},
        {"projection_version": "cad.entity-projection/3"},
    ):
        with pytest.raises(ValueError, match="invalid cursor"):
            _decode(cursor, **override)

    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    with pytest.raises(ValueError, match="invalid cursor"):
        _decode(tampered)


def test_cursor_rejects_short_secret_and_negative_offset():
    with pytest.raises(ValueError):
        encode_cursor(
            secret=b"short",
            owner_subject="alice",
            scene_id="scn-a",
            section="nodes",
            filters={},
            offset=0,
            projection_version="cad.entity-projection/2",
        )
    with pytest.raises(ValueError):
        encode_cursor(
            secret=SECRET,
            owner_subject="alice",
            scene_id="scn-a",
            section="nodes",
            filters={},
            offset=-1,
            projection_version="cad.entity-projection/2",
        )
