import math

import ezdxf
import pytest

from autocad_mcp.phase10_projection import (
    project_ezdxf_entities,
    project_ezdxf_entity,
)


def test_ezdxf_tier_a_projection_matches_r25_source_shape():
    doc = ezdxf.new("R2013")
    model = doc.modelspace()
    entities = [
        model.add_line((1, 2, 0), (3, 4, 0)),
        model.add_circle((5, 6, 0), 2),
        model.add_lwpolyline([(0, 0, 0.5), (4, 0, 0), (4, 2, -0.5)], format="xyb", close=True),
        model.add_arc((8, 9, 0), 3, 30, 120),
    ]

    projected = project_ezdxf_entities(entities)

    assert [item["type"] for item in projected] == [
        "LINE", "CIRCLE", "LWPOLYLINE", "ARC"
    ]
    assert all(item["geometry_status"] == "exact" for item in projected)
    assert projected[0]["geometry"] == {
        "start": [1.0, 2.0],
        "end": [3.0, 4.0],
        "start_elevation": 0.0,
        "end_elevation": 0.0,
    }
    assert projected[1]["geometry"]["normal"] == [0.0, 0.0, 1.0]
    assert projected[2]["geometry"]["bulges"] == [0.5, 0.0, -0.5]
    assert projected[2]["geometry"]["closed"] is True
    assert projected[3]["geometry"]["start_angle"] == pytest.approx(math.pi / 6)
    assert projected[3]["geometry"]["end_angle"] == pytest.approx(2 * math.pi / 3)
    assert all(item["live_dwg_authority"] is False for item in projected)


def test_ezdxf_projection_is_bounded_and_reports_unsupported():
    doc = ezdxf.new("R2013")
    model = doc.modelspace()
    unsupported = project_ezdxf_entity(model.add_point((1, 2)))

    assert unsupported["geometry_status"] == "unsupported"
    assert unsupported["geometry_reason"] == "entity_type_unsupported"
    assert unsupported["source_capabilities"] == []
    with pytest.raises(ValueError, match="between 1 and 5000"):
        project_ezdxf_entities([], limit=5_001)
