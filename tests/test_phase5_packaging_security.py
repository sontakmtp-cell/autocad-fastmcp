from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

POWERSHELL_BIN = shutil.which("pwsh") or shutil.which("powershell") or "powershell"


ROOT = Path(__file__).parents[1]
PACKAGING = ROOT / "native" / "autocad_managed_host" / "packaging"
VALIDATOR_PATH = ROOT / "scripts" / "validate-phase5-release.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validator():
    spec = importlib.util.spec_from_file_location("phase5_release_validator", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paths():
    return (
        PACKAGING / "phase5-release-families.json",
        PACKAGING / "phase5-runtime-policy.json",
        PACKAGING / "PackageContents.phase5.xml",
    )


def test_release_family_scaffold_is_fail_closed():
    _validator().validate(*_paths())


def test_unverified_family_cannot_claim_capability(tmp_path):
    manifest_path, policy_path, xml_path = _paths()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["families"][0]["certified_capabilities"] = ["drawing.observe.summary"]
    changed = tmp_path / "release.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="real family lab evidence"):
        _validator().validate(changed, policy_path, xml_path)


@pytest.mark.parametrize(
    "switch",
    ["managed_write", "lt_write", "high_risk", "advanced_lisp", "arbitrary_code"],
)
def test_risky_switches_cannot_default_on(tmp_path, switch):
    manifest_path, policy_path, xml_path = _paths()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["switches"][switch] = True
    changed = tmp_path / "policy.json"
    changed.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{switch} must default off"):
        _validator().validate(manifest_path, changed, xml_path)


def test_lt_never_loads_managed_host(tmp_path):
    manifest_path, policy_path, xml_path = _paths()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lt_compatibility"]["managed_host_loaded"] = True
    changed = tmp_path / "release.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="LT must remain File IPC"):
        _validator().validate(changed, policy_path, xml_path)


def test_packaging_scripts_have_no_publish_or_remote_execution_surface():
    build = (ROOT / "scripts" / "build-phase5-release-bundle.ps1").read_text(
        encoding="utf-8"
    )
    install = (ROOT / "scripts" / "install-phase5-release-bundle.ps1").read_text(
        encoding="utf-8"
    )
    combined = (build + install).lower()
    for forbidden in (
        "invoke-expression",
        "start-process",
        "invoke-webrequest",
        "github",
        "curl ",
        "http://",
        "https://",
    ):
        assert forbidden not in combined
    assert "LabOnly" in install
    assert "artifact hash mismatch" in combined


def test_signed_r25_release_is_certificate_store_bound_and_fail_closed():
    signing = (ROOT / "scripts" / "new-phase5-signed-r25-release.ps1").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "scripts" / "install-phase5-signed-r25.ps1").read_text(
        encoding="utf-8"
    )
    rollback = (ROOT / "scripts" / "rollback-phase5-signed-r25.ps1").read_text(
        encoding="utf-8"
    )
    combined = (signing + installer + rollback).lower()
    assert "cert:\\currentuser\\my" in signing.lower()
    assert "haskey" not in combined
    assert "hasprivatekey" in signing.lower()
    assert "1.3.6.1.5.5.7.3.3" in signing
    assert "production signing requires a timestamp server" in signing.lower()
    assert "__PHASE5_RELEASE_MANIFEST_SHA256__" in installer
    assert "signer does not match the release manifest" in installer.lower()
    for status in ("HashMismatch", "NotSigned", "NotSupported", "Incompatible"):
        assert status in installer
        assert status in rollback
    for allowed_lab_status in ("Valid", "NotTrusted", "UnknownError"):
        assert allowed_lab_status in installer
        assert allowed_lab_status in rollback
    assert "certificate chain processed.*terminated.*root certificate.*not trusted" in (
        installer
    )
    assert "certificate chain processed.*terminated.*root certificate.*not trusted" in (
        rollback
    )
    assert "has no Authenticode signer" in installer
    assert "has no Authenticode signer" in rollback
    assert "close autocad before" in combined
    assert "invoke-expression" not in combined
    assert "invoke-webrequest" not in combined


@pytest.mark.skipif(os.name != "nt", reason="PowerShell packaging is Windows-only")
@pytest.mark.parametrize(
    "script_name, context",
    [
        ("install-phase5-signed-r25.ps1", "Installer"),
        ("rollback-phase5-signed-r25.ps1", "Rollback"),
    ],
)
@pytest.mark.parametrize(
    "signature_status, signer_present, expected_error",
    [
        ("HashMismatch", True, "Authenticode status is unsafe: HashMismatch"),
        ("NotSigned", True, "Authenticode status is unsafe: NotSigned"),
        ("NotSupported", True, "Authenticode status is unsafe: NotSupported"),
        ("Incompatible", True, "Authenticode status is unsafe: Incompatible"),
        ("Valid", False, "has no Authenticode signer"),
        ("UnknownError", True, "lab Authenticode status is invalid: UnknownError"),
    ],
)
def test_lab_scripts_reject_unsafe_or_missing_signatures(
    tmp_path,
    script_name,
    context,
    signature_status,
    signer_present,
    expected_error,
):
    thumbprint = "A" * 40
    script = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
    release_root = tmp_path / "release"
    release_root.mkdir()
    receipt_path = tmp_path / "receipt.json"

    if script_name.startswith("install"):
        manifest_path = release_root / "release-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": "autocad-mcp.signed-release/1",
                    "release_version": "1.0.0",
                    "host_family": "R25",
                    "bundle_name": "AutocadMcp.ManagedHost.R25.bundle",
                    "lab_only": True,
                    "signing": {"certificate_thumbprint": thumbprint},
                    "artifacts": [],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        manifest_hash = _sha256(manifest_path)
        script = script.replace(
            "__PHASE5_RELEASE_MANIFEST_SHA256__", manifest_hash
        )
        invocation = (
            f"& '{(tmp_path / script_name).as_posix()}' "
            f"-ReleaseRoot '{release_root.as_posix()}' -LabOnly "
            f"-PluginsRoot '{(tmp_path / 'plugins').as_posix()}' "
            f"-ReceiptRoot '{(tmp_path / 'receipts').as_posix()}' "
            "-IsolatedTestRoot"
        )
    else:
        receipt_path.write_text(
            json.dumps(
                {
                    "schema": "autocad-mcp.install-receipt/2",
                    "status": "installed",
                    "lab_only": True,
                    "certificate_thumbprint": thumbprint,
                }
            ),
            encoding="utf-8",
        )
        invocation = (
            f"& '{(tmp_path / script_name).as_posix()}' "
            f"-ReceiptPath '{receipt_path.as_posix()}' -LabOnly "
            "-IsolatedTestRoot"
        )

    script_path = tmp_path / script_name
    script_path.write_text(script, encoding="utf-8")
    signer = (
        f"[pscustomobject]@{{Thumbprint='{thumbprint}'}}"
        if signer_present
        else "$null"
    )
    command = (
        "function Get-AuthenticodeSignature {"
        f" [pscustomobject]@{{Status='{signature_status}';"
        f" SignerCertificate={signer};"
        " TimeStamperCertificate=$null} };"
        "try { "
        f"{invocation};"
        " Write-Error 'Expected unsafe Authenticode status to be rejected'; exit 2"
        "} catch {"
        " [Console]::Error.WriteLine($_.Exception.Message); exit 1"
        "}"
    )
    result = subprocess.run(
        [POWERSHELL_BIN, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"{context} {expected_error}" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="PowerShell packaging is Windows-only")
def test_lab_installer_accepts_only_the_known_untrusted_root_error(tmp_path):
    thumbprint = "A" * 40
    release_root = tmp_path / "release"
    bundle_root = release_root / "AutocadMcp.ManagedHost.R25.bundle"
    bundle_root.mkdir(parents=True)
    manifest_path = release_root / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "autocad-mcp.signed-release/1",
                "release_version": "1.0.0",
                "host_family": "R25",
                "bundle_name": "AutocadMcp.ManagedHost.R25.bundle",
                "lab_only": True,
                "signing": {"certificate_thumbprint": thumbprint},
                "artifacts": [],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    script = (
        ROOT / "scripts" / "install-phase5-signed-r25.ps1"
    ).read_text(encoding="utf-8")
    script = script.replace(
        "__PHASE5_RELEASE_MANIFEST_SHA256__", _sha256(manifest_path)
    )
    script_path = tmp_path / "Install-Phase5R25.ps1"
    script_path.write_text(script, encoding="utf-8")
    plugins_root = tmp_path / "plugins"
    receipts_root = tmp_path / "receipts"
    status_message = (
        "A certificate chain processed, but terminated in a root certificate "
        "which is not trusted by the trust provider."
    )
    command = (
        "function Get-AuthenticodeSignature {"
        " [pscustomobject]@{Status='UnknownError';"
        f" StatusMessage='{status_message}';"
        f" SignerCertificate=[pscustomobject]@{{Thumbprint='{thumbprint}'}};"
        " TimeStamperCertificate=$null} };"
        f"& '{script_path.as_posix()}' "
        f"-ReleaseRoot '{release_root.as_posix()}' -LabOnly "
        f"-PluginsRoot '{plugins_root.as_posix()}' "
        f"-ReceiptRoot '{receipts_root.as_posix()}' -IsolatedTestRoot"
    )
    result = subprocess.run(
        [POWERSHELL_BIN, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (plugins_root / bundle_root.name).is_dir()


def test_upgrade_and_rollback_harness_is_isolated_and_hash_exact():
    rehearsal = (ROOT / "scripts" / "test-phase5-install-rollback.ps1").read_text(
        encoding="utf-8"
    )
    vm = (ROOT / "scripts" / "test-phase5-clean-vm-rollback.ps1").read_text(
        encoding="utf-8"
    )
    assert "Clean rehearsal root must not already exist" in rehearsal
    assert "exact previous bundle" in rehearsal
    assert "clean-install rollback" in rehearsal.lower()
    assert "New-PSSession -VMName" in vm
    assert "does not change VM power state" in vm
    assert "Invoke-Expression" not in rehearsal + vm


@pytest.mark.skipif(os.name != "nt", reason="PowerShell packaging is Windows-only")
def test_phase5_signing_and_rollback_scripts_parse():
    scripts = [
        "new-phase5-lab-signing-certificate.ps1",
        "new-phase5-signed-r25-release.ps1",
        "install-phase5-signed-r25.ps1",
        "rollback-phase5-signed-r25.ps1",
        "test-phase5-install-rollback.ps1",
        "test-phase5-clean-vm-rollback.ps1",
    ]
    for name in scripts:
        path = (ROOT / "scripts" / name).as_posix().replace("'", "''")
        command = (
            "$tokens=$null;$errors=$null;"
            f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',"
            "[ref]$tokens,[ref]$errors)|Out-Null;"
            "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
        )
        subprocess.run(
            [POWERSHELL_BIN, "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
