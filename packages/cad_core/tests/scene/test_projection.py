import math

from cad_core.scene.projection import project_entities


def test_projection_is_explicit_for_tier_a_and_bad_source_geometry():
    projected = project_entities(
        [
            {
                "entity_id": "A",
                "entity_type": "ARC",
                "geometry": {
                    "center": [0, 0],
                    "radius": 5,
                    "start_angle_radians": 0,
                    "end_angle_radians": math.pi / 2,
                    "normal": [0, 0, 1],
                },
            },
            {
                "entity_id": "P",
                "entity_type": "LWPOLYLINE",
                "geometry": {
                    "vertices": [
                        {"x": 0, "y": 0, "bulge": 0},
                        {"x": 10, "y": 0, "bulge": 1},
                        {"x": 10, "y": 10, "bulge": 0},
                        {"x": 0, "y": 10, "bulge": -1},
                    ],
                    "closed": True,
                    "elevation": 0,
                    "normal": [0, 0, 1],
                },
            },
            {
                "entity_id": "T",
                "entity_type": "LWPOLYLINE",
                "geometry_truncated": True,
            },
            {"entity_id": "U", "entity_type": "ELLIPSE"},
            {
                "entity_id": "N",
                "entity_type": "CIRCLE",
                "geometry": {
                    "center": [0, 0],
                    "radius": 2,
                    "normal": [1, 0, 0],
                },
            },
        ]
    )

    assert [node.source_entity_id for node in projected] == ["A", "N", "P", "T", "U"]
    assert projected[0].node_id.startswith("nod_")
    assert projected[0].geometry.start_angle_radians == 0
    assert projected[2].bounds.max_x > 10
    assert [node.geometry_status for node in projected] == [
        "exact",
        "invalid",
        "exact",
        "truncated",
        "unsupported",
    ]
