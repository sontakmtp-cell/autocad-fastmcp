# Phase 5 R25 signing and clean rollback runbook

Date: 2026-07-25

## Current acceptance scope

Per operator direction, older AutoCAD release families and a real LT 2024+
device are deferred from the current Phase 5 acceptance decision. They remain
uncertified and must not appear as supported in a production manifest.

This runbook closes the repository-side R25 signing and rollback engineering
gap. It does not manufacture a publicly trusted certificate or claim that a
host rehearsal is a clean-VM result.

## Certificate policy

Production releases require a CA-issued code-signing certificate with:

- Code Signing EKU `1.3.6.1.5.5.7.3.3`;
- a private key held by the reviewed build identity, preferably hardware-backed
  and non-exportable;
- a certificate chain trusted on the target Windows machine;
- an Authenticode timestamp so an already-signed release remains verifiable
  after certificate expiry;
- documented owner, expiry alert, rotation, revocation and incident procedure.

The release builder reads an exact thumbprint from
`Cert:\CurrentUser\My`. It never reads a PFX password from a command line,
downloads a certificate, exports a private key, or uploads an artifact.
Production mode rejects a missing timestamp server and rejects any signature
whose status is not `Valid` or has no timestamp.

Microsoft documents `Set-AuthenticodeSignature` for SIP-supported files and
`Get-AuthenticodeSignature` for verification. `New-SelfSignedCertificate` is
used here only for test evidence and is not a production trust source:

- https://learn.microsoft.com/powershell/module/microsoft.powershell.security/set-authenticodesignature
- https://learn.microsoft.com/powershell/module/microsoft.powershell.security/get-authenticodesignature
- https://learn.microsoft.com/powershell/module/pki/new-selfsignedcertificate

## Build a signed R25 release

Build the unsigned R25 input:

```powershell
.\scripts\build-phase5-managed-host.ps1
```

For a lab-only certificate:

```powershell
$lab = .\scripts\new-phase5-lab-signing-certificate.ps1
```

Create a lab release:

```powershell
.\scripts\new-phase5-signed-r25-release.ps1 `
  -UnsignedBundlePath .\dist\phase5-managed-host\AutocadMcp.ManagedHost.R25.bundle `
  -CertificateThumbprint $lab.thumbprint `
  -ReleaseVersion 0.1.0-lab1 `
  -LabOnly
```

For production, omit `-LabOnly`, use the reviewed CA certificate thumbprint and
pass the approved HTTP Authenticode timestamp endpoint. The builder signs both
Managed Host assemblies plus the installer and rollback scripts. It then
creates:

- `release-manifest.json` with bounded paths, post-sign hashes and signer IDs;
- a package manifest containing the post-sign package hash;
- `signature-evidence.json`;
- a signed installer that embeds the exact release-manifest SHA-256.

The installer rejects manifest tampering, artifact tampering, signer mismatch,
an invalid production chain, a missing production timestamp and installation
while AutoCAD is running.

## Install, upgrade and rollback

Run `Install-Phase5R25.ps1` from the signed release directory. A lab release
requires explicit `-LabOnly`. Before upgrade, the installer moves the current
bundle to a unique previous-known-good backup. It writes a versioned receipt
below:

`%LOCALAPPDATA%\KythuatVang\AutoCADMcp\install-receipts`

Rollback requires that exact receipt:

```powershell
.\Rollback-Phase5R25.ps1 -ReceiptPath <receipt-path>
```

Rollback moves the current release to a recoverable displaced directory and
restores the exact backup. When rolling back a clean install with no prior
bundle, the destination becomes absent. Path checks prevent a modified receipt
from escaping the reviewed plugins root.

## Local isolated rehearsal

The local rehearsal uses a new, non-Autodesk root and therefore does not touch
the installed AutoCAD bundle:

```powershell
.\scripts\test-phase5-install-rollback.ps1 `
  -ReleaseV1Root .\dist\phase5-signed-r25-v1 `
  -ReleaseV2Root .\dist\phase5-signed-r25-v2 `
  -WorkRoot <new-empty-path> `
  -EvidencePath .\dist\phase5-clean-install-rollback-local.json
```

It verifies clean install, upgrade, exact-hash restore of v1, and removal after
rolling back the original clean install.

## Clean Hyper-V VM run

`test-phase5-clean-vm-rollback.ps1` uses PowerShell Direct, documented by
Microsoft for Windows Hyper-V guests:

https://learn.microsoft.com/windows-server/virtualization/hyper-v/powershell-direct

The VM must already be a clean, running, disposable Windows guest. The harness
does not start, stop, checkpoint or restore it silently:

```powershell
$credential = Get-Credential
.\scripts\test-phase5-clean-vm-rollback.ps1 `
  -VMName Phase4-Win11-Clean `
  -Credential $credential `
  -ReleaseV1Root .\dist\phase5-signed-r25-v1 `
  -ReleaseV2Root .\dist\phase5-signed-r25-v2 `
  -EvidencePath .\dist\phase5-clean-vm-evidence.json
```

The current non-elevated Codex session cannot run `Get-VM` on this machine:
Hyper-V reports insufficient authorization. A fresh clean-VM evidence file must
therefore be produced from an authorized operator session before production
certification.
