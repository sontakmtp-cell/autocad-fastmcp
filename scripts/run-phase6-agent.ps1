[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$AllowedDeviceId,
    [string]$PolicyVersion = "phase6-policy/1",
    [switch]$EnableManagedWrite,
    [switch]$Headless,
    [string]$ConfigPath = (
        Join-Path $env:LOCALAPPDATA (
            "Kythuatvang\AutoCADAgent\agent-config.json"
        )
    )
)

$ErrorActionPreference = "Stop"
if ($AllowedDeviceId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "AllowedDeviceId is invalid."
}
$env:AUTOCAD_MCP_RUNTIME_MODE = "managed_dotnet"
$env:AUTOCAD_MCP_MANAGED_HOST_ENABLED = "1"
$env:AUTOCAD_MCP_ALLOW_FULL_COMPAT_FALLBACK = "0"
$env:AUTOCAD_MCP_LT_RUNTIME_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V0_ENABLED = "1"
$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_LT_WRITE_ENABLED = "0"
$env:AUTOCAD_AGENT_WRITE_LOCK_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS = $AllowedDeviceId
$env:AUTOCAD_MCP_PROGRAM_POLICY_VERSION = $PolicyVersion

$env:AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED = "0"
$env:AUTOCAD_MCP_CHECKPOINT_V2_ENABLED = "0"
$env:AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST = if ($EnableManagedWrite) { "create-equivalent-v1,autocad.pack.phase8.create,cad.program/1.0-create-core" } else { "" }
$env:AUTOCAD_MCP_ROLLOUT_POLICY_EPOCH = if ($EnableManagedWrite) { "1" } else { "0" }

$arguments = @{
    ConfigPath = $ConfigPath
}
if ($Headless) {
    $arguments.Headless = $true
}
& (Join-Path $PSScriptRoot "run-phase5-agent.ps1") @arguments
exit $LASTEXITCODE
