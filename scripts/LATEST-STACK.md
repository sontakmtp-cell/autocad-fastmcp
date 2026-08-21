# Canonical latest stack entrypoints

Use these phase-neutral scripts for normal development and lab operation. Phase-numbered scripts remain compatibility/building blocks and should not be chosen by operators based on their names.

## Desktop Agent

Build the current standalone Desktop Agent:

```powershell
.\scripts\build-latest-agent.ps1
```

Default output:

```text
dist\latest-agent\app\KythuatvangAutoCADAgent.exe
```

The canonical build defaults to `mingw64` because Python 3.12 requires MSVC 14.3+ and older installed MSVC toolchains can otherwise be selected by Nuitka auto-detection.

Run the current Agent in read-only mode:

```powershell
.\scripts\run-latest-agent.ps1
```

Run from source:

```powershell
.\scripts\run-latest-agent.ps1 -Source
```

Managed .NET R25 is authoritative in the canonical launcher. Full AutoLISP compatibility fallback is disabled so a capability advertised by the Managed Host cannot silently execute on a less capable read runtime.

Managed write remains opt-in:

```powershell
.\scripts\run-latest-agent.ps1 `
  -EnableManagedWrite `
  -AllowedDeviceId "<device-id>"
```

## Gateway

The latest public Gateway is **not** a `phase10` profile. Phase 10 scene intelligence is a feature layer on the current `phase9_workflow` profile.

Run the latest Gateway:

```powershell
.\scripts\run-latest-gateway.ps1 `
  -CursorSigningSecret "<at-least-32-byte-secret>"
```

The launcher enables the Phase 9 workflow/skill platform and Phase 10 scene engine/resources/tools. It requires the trusted Phase 8 runtime/compiler pins through parameters or their existing `AUTOCAD_MCP_PHASE8_*` environment variables.

Managed write is disabled by default. To enable it, explicitly provide the device allowlist and operation-pack allowlist:

```powershell
.\scripts\run-latest-gateway.ps1 `
  -CursorSigningSecret "<at-least-32-byte-secret>" `
  -EnableManagedWrite `
  -AllowedDeviceIds "<device-id>" `
  -OperationPackAllowlist "<trusted-pack-list>"
```

## Compatibility mapping

| Canonical entrypoint | Current implementation beneath it |
| --- | --- |
| `build-latest-agent.ps1` | `build-phase5-agent.ps1` packaging pipeline |
| `run-latest-agent.ps1` | current Agent source/executable via `run-phase5-agent.ps1` |
| `run-latest-gateway.ps1` | `python -m autocad_gateway`, profile `phase9_workflow`, Phase 10 flags enabled |

The mapping is an implementation detail. Future Phase 11+ work should update the canonical wrappers while keeping operator commands stable whenever possible.
