"""Build the three independent Phase 10 live DXF/DWG fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import ezdxf
from ezdxf import units


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "fixtures" / "phase10" / "live"
DEFAULT_CORE_CONSOLE = (
    Path("C:/Program Files/Autodesk/AutoCAD 2025") / "accoreconsole.exe"
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _new_document():
    document = ezdxf.new("R2018", setup=True)
    document.units = units.MM
    for name, color in (
        ("PART", 7),
        ("HOLES", 1),
        ("SLOTS", 3),
        ("FEATURES", 4),
        ("ANOMALIES", 2),
        ("VALID", 5),
        ("REFERENCE", 8),
    ):
        document.layers.add(name, color=color)
    return document


def _drawing_a(path: Path) -> None:
    document = _new_document()
    space = document.modelspace()
    space.add_lwpolyline(
        [(0, 0), (120, 0), (120, 80), (0, 80)],
        close=True,
        dxfattribs={"layer": "PART"},
    )
    for center in ((20, 20), (100, 20), (20, 60), (100, 60)):
        space.add_circle(center, 5, dxfattribs={"layer": "HOLES"})
    space.add_circle((60, 40), 3, dxfattribs={"layer": "REFERENCE"})
    document.saveas(path)


def _drawing_b(path: Path) -> None:
    document = _new_document()
    space = document.modelspace()
    space.add_arc(
        (0, 0),
        5,
        start_angle=90,
        end_angle=270,
        dxfattribs={"layer": "SLOTS"},
    )
    space.add_arc(
        (20, 0),
        5,
        start_angle=270,
        end_angle=90,
        dxfattribs={"layer": "SLOTS"},
    )
    space.add_line((0, 5), (20, 5), dxfattribs={"layer": "SLOTS"})
    space.add_line((0, -5), (20, -5), dxfattribs={"layer": "SLOTS"})
    space.add_circle((50, 0), 3, dxfattribs={"layer": "FEATURES"})
    space.add_circle((50, 0), 6, dxfattribs={"layer": "FEATURES"})
    space.add_circle((50.1, 0), 9, dxfattribs={"layer": "REFERENCE"})
    space.add_lwpolyline(
        [(70, -5, 0), (90, -5, 0.95), (90, 5, 0), (70, 5, 0.95)],
        format="xyb",
        close=True,
        dxfattribs={"layer": "REFERENCE"},
    )
    document.saveas(path)


def _drawing_c(path: Path) -> None:
    document = _new_document()
    space = document.modelspace()
    space.add_line((0, 0), (10, 0), dxfattribs={"layer": "ANOMALIES"})
    space.add_line((10, 0), (0, 0), dxfattribs={"layer": "ANOMALIES"})
    space.add_line((20, 0), (20.0000001, 0), dxfattribs={"layer": "ANOMALIES"})
    space.add_line((30, 0), (40, 0), dxfattribs={"layer": "ANOMALIES"})
    space.add_line((40, 0), (45, 5), dxfattribs={"layer": "ANOMALIES"})
    space.add_lwpolyline(
        [(55, 0), (65, 10), (55, 10), (65, 0)],
        close=True,
        dxfattribs={"layer": "ANOMALIES"},
    )
    space.add_circle((75, 0), 0.0000001, dxfattribs={"layer": "ANOMALIES"})
    space.add_lwpolyline(
        [(85, 0), (105, 0), (105, 10), (85, 10)],
        close=True,
        dxfattribs={"layer": "VALID"},
    )
    space.add_circle((95, 5), 2, dxfattribs={"layer": "VALID"})
    document.saveas(path)


def _convert_to_dwg(dxf_path: Path, dwg_path: Path, core_console: Path) -> None:
    script = (
        "_.FILEDIA\n0\n"
        "_.CMDDIA\n0\n"
        "_.SAVEAS\n2018\n"
        f'"{dwg_path}"\n'
    )
    with tempfile.TemporaryDirectory(prefix="phase10-fixture-") as directory:
        script_path = Path(directory) / "saveas.scr"
        script_path.write_text(script, encoding="ascii")
        subprocess.run(
            [
                str(core_console),
                "/i",
                str(dxf_path),
                "/s",
                str(script_path),
                "/l",
                "en-US",
            ],
            check=True,
        )
    if not dwg_path.is_file():
        raise RuntimeError(f"AutoCAD did not create {dwg_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--core-console", type=Path, default=DEFAULT_CORE_CONSOLE)
    parser.add_argument("--dxf-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    builders = {
        "drawing-a": _drawing_a,
        "drawing-b": _drawing_b,
        "drawing-c": _drawing_c,
    }
    manifest = {
        "schema_version": "autocad-mcp.phase10-live-fixtures/1",
        "fixtures": [],
    }
    for name, builder in builders.items():
        dxf_path = output / f"phase10-{name}.dxf"
        dwg_path = output / f"phase10-{name}.dwg"
        if not args.force and (dxf_path.exists() or dwg_path.exists()):
            raise RuntimeError(f"fixture already exists: {name}")
        if args.force:
            dxf_path.unlink(missing_ok=True)
            dwg_path.unlink(missing_ok=True)
        builder(dxf_path)
        if not args.dxf_only:
            _convert_to_dwg(dxf_path, dwg_path, args.core_console.resolve())
        manifest["fixtures"].append(
            {
                "fixture_id": f"phase10-{name}-r25/1",
                "dxf": dxf_path.name,
                "dwg": dwg_path.name,
                "dxf_sha256": _sha256(dxf_path),
                "dwg_sha256": None if args.dxf_only else _sha256(dwg_path),
                "independent_source": True,
                "purpose": {
                    "drawing-a": "hole and repeated-hole pattern with one non-pattern circle",
                    "drawing-b": "exact slot and concentric group with tolerance negatives",
                    "drawing-c": "degenerate, duplicate, open and self-intersecting cleanup cases",
                }[name],
                "expected": {
                    "drawing-a": {
                        "features": ["hole", "repeated_hole_pattern"],
                        "negative": ["non_pattern_circle_excluded"],
                    },
                    "drawing-b": {
                        "features": ["slot", "concentric_group"],
                        "negative": [
                            "near_slot_excluded",
                            "near_concentric_outside_tolerance",
                        ],
                    },
                    "drawing-c": {
                        "issues": [
                            "degenerate_geometry",
                            "duplicate_geometry",
                            "open_contour",
                            "self_intersection",
                        ],
                        "negative": ["valid_geometry_not_flagged_for_cleanup"],
                    },
                }[name],
            }
        )
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
