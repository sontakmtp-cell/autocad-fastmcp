"""Run focused deterministic Phase 10 conformance and security gates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "packages/contracts/tests/test_phase10_contracts.py",
    "packages/cad_core/tests/scene",
    "services/gateway/tests/phase10",
    "apps/desktop_agent/tests/test_phase10_projection.py",
    "tests/test_phase10_projection.py",
)


def validate_public_surface_counters() -> None:
    snapshots = ROOT / "services" / "gateway" / "snapshots"
    tools = json.loads((snapshots / "phase10_tools.json").read_text(encoding="utf-8"))
    resources = json.loads(
        (snapshots / "phase10_resources.json").read_text(encoding="utf-8")
    )
    expected_tools = {"cad_build_scene", "cad_query_scene"}
    if {item["name"] for item in tools} != expected_tools:
        raise SystemExit("Phase 10 must expose exactly two reviewed scene tools")
    if len(resources) != 7:
        raise SystemExit("Phase 10 must expose exactly seven bounded scene resources")
    for item in tools:
        annotations = item["annotations"]
        if (
            annotations.get("readOnlyHint") is not True
            or annotations.get("destructiveHint") is not False
            or annotations.get("openWorldHint") is not False
        ):
            raise SystemExit(f"{item['name']} is not closed and read-only")
    print(
        "Deterministic public security counters: "
        f"tools={len(tools)}, resources={len(resources)}, "
        "destructive_tools=0, open_world_tools=0.",
        flush=True,
    )


def report_headless_performance_observation() -> None:
    sys.path.insert(0, str(ROOT / "packages" / "cad_core" / "src"))
    from cad_core.scene import SceneBuildContext, build_scene

    def entity(
        entity_id: str,
        entity_type: str,
        geometry: dict[str, object],
        capability: str,
    ) -> dict[str, object]:
        return {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "layer": "0",
            "space": "model",
            "geometry": geometry,
            "fingerprint": f"sha256:{entity_id:0>64}",
            "source_runtime": "phase10_conformance",
            "source_capabilities": [capability],
        }

    source = [
        entity(
            "PLATE",
            "LWPOLYLINE",
            {
                "vertices": [
                    {"x": 0, "y": 0, "bulge": 0},
                    {"x": 100, "y": 0, "bulge": 0},
                    {"x": 100, "y": 60, "bulge": 0},
                    {"x": 0, "y": 60, "bulge": 0},
                ],
                "closed": True,
                "elevation": 0,
                "normal": [0, 0, 1],
            },
            "entity.geometry.polyline/1",
        ),
        *[
            entity(
                f"H{index}",
                "CIRCLE",
                {"center": center, "radius": 5, "normal": [0, 0, 1]},
                "entity.geometry.circle/1",
            )
            for index, center in enumerate(
                ((20, 15), (80, 15), (20, 45), (80, 45)),
                start=1,
            )
        ],
    ]
    context = SceneBuildContext(
        "snapshot-headless-conformance",
        "device-headless-conformance",
        "document-headless-conformance",
        "revision-headless-conformance",
        source_capabilities=(
            "entity.geometry.circle/1",
            "entity.geometry.polyline/1",
        ),
        drawing_units="mm",
    )
    tracemalloc.start()
    scene = build_scene(source, context)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    stats = scene.stats
    observation = {
        "fixture": "headless_plate_four_holes",
        "source_entities": stats.source_entities,
        "projected_nodes": stats.projected_nodes,
        "spatial_cells": stats.spatial_cells,
        "relation_candidates": stats.relation_candidates,
        "relations": stats.relations,
        "contours": stats.contours,
        "features": stats.features,
        "issues": stats.issues,
        "projected_bytes": stats.projected_bytes,
        "scene_bytes": stats.scene_bytes,
        "peak_python_bytes": peak_bytes,
        "build_seconds_observation_only": round(stats.build_seconds, 6),
        "complete": scene.complete,
    }
    print(
        "Headless performance observation (wall time is not a CI gate): "
        + json.dumps(observation, sort_keys=True),
        flush=True,
    )


def main() -> int:
    missing = [target for target in TARGETS if not (ROOT / target).exists()]
    if missing:
        raise SystemExit(f"Missing Phase 10 conformance targets: {', '.join(missing)}")

    validate_public_surface_counters()
    report_headless_performance_observation()
    basetemp = ROOT / ".pytest_cache" / "phase10-conformance"
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        str(ROOT / path)
        for path in (
            "packages/cad_core/src",
            "packages/contracts/src",
            "services/gateway/src",
            "apps/desktop_agent/src",
            "src",
        )
    )
    command = [
        "uv",
        "run",
        "--project",
        "services/gateway",
        "--group",
        "test",
        "python",
        "-m",
        "pytest",
        "-q",
        f"--basetemp={basetemp}",
        *TARGETS,
    ]
    print(
        "Running contracts, scene engine, Gateway, runtime projection, "
        "owner/cursor/redaction, and deterministic budget-counter tests.",
        flush=True,
    )
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
