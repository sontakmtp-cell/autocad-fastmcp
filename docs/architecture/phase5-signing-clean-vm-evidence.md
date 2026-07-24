# Phase 5 R25 signing and rollback evidence

Date: 2026-07-25

## Scope decision

The operator temporarily deferred older AutoCAD families and real LT 2024+
certification. They are not treated as current blockers, but remain explicitly
unsupported/uncertified.

This evidence covers R25 certificate handling, Authenticode packaging,
install/upgrade/rollback and the clean-VM execution boundary.

## Authenticode lab run

A 30-day, non-exportable, current-user test certificate was created:

- subject: `CN=KythuatVang AutoCAD MCP Phase5 Lab`
- thumbprint: `1383538E693BFC0D915872EFEB51735DBD8FBD8A`
- EKU: Code Signing `1.3.6.1.5.5.7.3.3`
- purpose: test only

Two R25 releases were produced:

| Release | Release-manifest SHA-256 |
|---|---|
| `0.1.0-lab1` | `0d3e157f485d39590d3d5ed27abc707eee6d7078ebfcf6b716fd4d44edeabe01` |
| `0.1.1-lab2` | `95694ace5f2c92c3426dc09576a3cc219a66d0a643220ce77919a67fe145b2eb` |

Both Managed Host DLLs and the install/rollback scripts contain Authenticode
signatures from that exact thumbprint. Signature status is `UnknownError`
because the test certificate is deliberately self-signed and was not added to
a trusted root. No timestamp was requested in lab mode. This is expected lab
evidence, not production trust.

After the rehearsal, the exact lab certificate and its non-exportable private
key were removed from `CurrentUser\My`. The signed lab artifacts retain the
embedded public certificate for evidence; a future lab run must create a new
short-lived test certificate.

Production mode is fail-closed: it requires a CA-trusted certificate from the
reviewed certificate store, a timestamp server, `Valid` Authenticode status and
a timestamp on every DLL and installer/rollback script.

## Install, upgrade and rollback rehearsal

The rehearsal started with a new absent root outside the Autodesk plugins
directory. Results:

| Check | Result |
|---|---|
| Clean install v1 | Pass |
| Upgrade v1 → v2 with previous-known-good backup | Pass |
| Rollback v2 → exact v1 | Pass |
| Rollback original clean install → destination absent | Pass |
| Modified DLL byte rejected | Pass, `Release artifact hash mismatch` |
| Modified release manifest rejected | Pass, installer-bound manifest hash mismatch |
| Installed bundle changed before rollback | Pass, automatic rollback stopped |
| Production signing without timestamp | Pass, signing stopped before release creation |
| Lab release installed without `-LabOnly` | Pass, installer stopped before file changes |

Exact aggregate hashes:

- v1 installed bundle:
  `7513400f11d09d4ffb0917296ea3f20a3aebe14593f41bea219ec0719531ef22`
- v2 installed bundle:
  `f63221d1041b1d25fbec105ec4c892353000635b47f25ff48a278eb7ed37687e`
- restored v1:
  `7513400f11d09d4ffb0917296ea3f20a3aebe14593f41bea219ec0719531ef22`

Machine evidence:

- OS: `Microsoft Windows NT 10.0.26200.0`
- PowerShell: `7.6.4`
- evidence artifact:
  `dist/phase5-clean-install-rollback-local.json`

The rehearsal did not touch the real Autodesk `ApplicationPlugins` directory.

## Automated regression

- repository suite: `396 passed, 1 skipped`
- Desktop Agent: `49 passed`
- Managed Host Core: `24 passed`
- packaging/signing safety: `12 passed`
- six signing/install/rollback PowerShell scripts: parser pass
- Phase 5 release validator: pass
- `git diff --check`: pass

The single skipped test and existing Python dependency deprecation warnings are
unrelated to the signing and rollback changes.

## Clean-VM boundary

The machine has Hyper-V and prior project evidence identifies
`Phase4-Win11-Clean`, but the current non-elevated session receives:

`You do not have the required permission to complete this task.`

Therefore a fresh Hyper-V result is not claimed. The repository now includes
`scripts/test-phase5-clean-vm-rollback.ps1`, which uses PowerShell Direct,
copies both signed releases into a new guest root, runs the same exact-hash
rehearsal and copies the evidence JSON back. It does not silently change VM
power or checkpoint state.

## Remaining production inputs

Repository engineering for R25 signing and rollback is complete. Production
certification still needs:

1. a CA-issued code-signing certificate and approved private-key custody;
2. a trusted Authenticode timestamp endpoint;
3. an authorized run against a fresh/disposable clean Windows VM;
4. malware/SBOM/build-provenance approval.

Until these external inputs exist, `managed_write` remains off and the
self-signed artifacts must stay lab-only.
