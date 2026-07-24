"""Fail-closed validation for the Phase 5 release-family lab scaffold."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


FAMILIES = {
    "R22": ("R22.0", "R22.0", "net46"),
    "R23": ("R23.0", "R23.1", "net47"),
    "R24": ("R24.0", "R24.3", "net48"),
    "R25": ("R25.0", "R25.1", "net8.0-windows"),
}
ALLOWED_CERTIFICATION = {"not_built", "lab_read_only_2025"}
REQUIRED_SWITCHES = {
    "managed_read",
    "lt_read",
    "managed_write",
    "lt_write",
    "high_risk",
    "advanced_lisp",
    "arbitrary_code",
    "runtime_fallback",
}
SENSITIVE_TELEMETRY = {
    "owner_subject",
    "access_token",
    "device_token",
    "pipe_secret",
    "document_path",
    "drawing_content",
    "raw_lisp",
    "cad_program",
    "stack_trace",
}


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: root must be an object")
    return value


def _safe_module_path(value: object, family: str) -> bool:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return (
        value == f"./Contents/{family}/AutocadMcp.Host.{family}.dll"
        and ".." not in path.parts
        and not path.is_absolute()
    )


def validate(manifest_path: Path, policy_path: Path, xml_path: Path) -> None:
    manifest = _load_json(manifest_path)
    policy = _load_json(policy_path)
    if manifest.get("schema") != "autocad-mcp.release-families/1":
        raise ValueError("release manifest schema mismatch")
    bundle = manifest.get("bundle")
    if not isinstance(bundle, dict):
        raise ValueError("bundle metadata is required")
    if (
        bundle.get("lab_only") is not True
        or bundle.get("signed") is not False
        or bundle.get("production_publish_allowed") is not False
    ):
        raise ValueError("Phase 5 scaffold must remain unsigned, lab-only, and unpublished")

    rows = manifest.get("families")
    if not isinstance(rows, list) or {row.get("id") for row in rows if isinstance(row, dict)} != set(FAMILIES):
        raise ValueError("release families must be exactly R22, R23, R24, and R25")
    for row in rows:
        family = row["id"]
        series_min, series_max, framework = FAMILIES[family]
        if (row.get("series_min"), row.get("series_max"), row.get("target_framework")) != (
            series_min,
            series_max,
            framework,
        ):
            raise ValueError(f"{family}: release-family range/runtime mismatch")
        if not _safe_module_path(row.get("module"), family):
            raise ValueError(f"{family}: module must be the reviewed relative family DLL")
        if row.get("certification") not in ALLOWED_CERTIFICATION:
            raise ValueError(f"{family}: certification claim is not allowed")
        if row.get("write_enabled") is not False:
            raise ValueError(f"{family}: write must remain disabled in the release scaffold")
        capabilities = row.get("certified_capabilities")
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ValueError(f"{family}: certified_capabilities must be a string list")
        if family != "R25" and capabilities:
            raise ValueError(f"{family}: capabilities require real family lab evidence")

    lt = manifest.get("lt_compatibility")
    if not isinstance(lt, dict):
        raise ValueError("LT compatibility metadata is required")
    if (
        lt.get("runtime_id") != "autolisp_file_ipc"
        or lt.get("managed_host_loaded") is not False
        or lt.get("real_lab_required") is not True
        or lt.get("certification") != "automated_regression_only"
    ):
        raise ValueError("LT must remain File IPC only and uncertified without a real lab")

    if policy.get("schema") != "autocad-mcp.runtime-policy/1":
        raise ValueError("runtime policy schema mismatch")
    switches = policy.get("switches")
    if not isinstance(switches, dict) or set(switches) != REQUIRED_SWITCHES:
        raise ValueError("runtime kill switches are incomplete")
    if any(not isinstance(value, bool) for value in switches.values()):
        raise ValueError("runtime kill switches must be booleans")
    for name in ("managed_write", "lt_write", "high_risk", "advanced_lisp", "arbitrary_code"):
        if switches[name]:
            raise ValueError(f"{name} must default off")

    telemetry = policy.get("telemetry")
    if not isinstance(telemetry, dict):
        raise ValueError("telemetry policy is required")
    dimensions = telemetry.get("dimensions")
    prohibited = telemetry.get("prohibited")
    if not isinstance(dimensions, list) or not isinstance(prohibited, list):
        raise ValueError("telemetry fields must be lists")
    if set(dimensions) & SENSITIVE_TELEMETRY:
        raise ValueError("sensitive telemetry dimension is not allowed")
    if not SENSITIVE_TELEMETRY.issubset(set(prohibited)):
        raise ValueError("telemetry prohibited list is incomplete")

    root = ElementTree.parse(xml_path).getroot()
    if root.tag != "ApplicationPackage":
        raise ValueError("PackageContents root is invalid")
    entries = root.findall("./Components/ComponentEntry")
    if len(entries) != 4:
        raise ValueError("PackageContents must contain four family components")
    seen: set[str] = set()
    for entry in entries:
        module = entry.get("ModuleName", "")
        match = re.fullmatch(r"\./Contents/(R2[2-5])/AutocadMcp\.Host\.\1\.dll", module)
        if match is None:
            raise ValueError("PackageContents contains an unreviewed module path")
        family = match.group(1)
        requirements = entry.find("RuntimeRequirements")
        if requirements is None:
            raise ValueError(f"{family}: RuntimeRequirements are missing")
        expected = FAMILIES[family]
        if (
            requirements.get("OS") != "Win64"
            or requirements.get("Platform") != "AutoCAD|ACADM"
            or "LT" in requirements.get("Platform", "").upper()
            or requirements.get("SeriesMin") != expected[0]
            or requirements.get("SeriesMax") != expected[1]
        ):
            raise ValueError(f"{family}: unsafe or mismatched runtime requirements")
        seen.add(family)
    if seen != set(FAMILIES):
        raise ValueError("PackageContents family coverage mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    packaging = root / "native" / "autocad_managed_host" / "packaging"
    parser.add_argument("--manifest", type=Path, default=packaging / "phase5-release-families.json")
    parser.add_argument("--policy", type=Path, default=packaging / "phase5-runtime-policy.json")
    parser.add_argument("--package-xml", type=Path, default=packaging / "PackageContents.phase5.xml")
    args = parser.parse_args()
    try:
        validate(args.manifest, args.policy, args.package_xml)
    except (OSError, ValueError, json.JSONDecodeError, ElementTree.ParseError) as error:
        print(f"phase5 release validation failed: {error}", file=sys.stderr)
        return 1
    print("phase5 release validation passed (lab scaffold; no certification implied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
