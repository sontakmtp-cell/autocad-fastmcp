[CmdletBinding()]
param(
    [string]$AllowedDeviceId = "",
    [string]$PolicyVersion = "phase6-policy/1",
    [string]$OperationPackAllowlist = "create-equivalent-v1,autocad.pack.phase8.create,cad.program/1.0-create-core",
    [switch]$EnableManagedWrite,
    [switch]$Headless,
    [switch]$PairOnly,
    [switch]$Source,
    [string]$ConfigPath = "",
    [string]$AgentExe = ""
)

$ErrorActionPreference = "Stop"
# The canonical launcher owns runtime configuration. Prevent stale local .env
# files from influencing the latest Agent or emitting parse warnings.
$env:PYTHON_DOTENV_DISABLED = "1"

if ($EnableManagedWrite) {
    if ([string]::IsNullOrWhiteSpace($AllowedDeviceId)) {
        throw "EnableManagedWrite requires -AllowedDeviceId."
    }
    if ($AllowedDeviceId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw "AllowedDeviceId is invalid."
    }
    if ([string]::IsNullOrWhiteSpace($OperationPackAllowlist)) {
        throw "EnableManagedWrite requires a non-empty OperationPackAllowlist."
    }
}
elseif (-not [string]::IsNullOrWhiteSpace($AllowedDeviceId) -and
    $AllowedDeviceId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "AllowedDeviceId is invalid."
}

# Canonical latest runtime policy: Managed .NET R25 is authoritative. The
# AutoLISP compatibility path remains installed but is not allowed to silently
# replace the managed runtime for this launcher.
$env:AUTOCAD_MCP_RUNTIME_MODE = "managed_dotnet"
$env:AUTOCAD_MCP_MANAGED_HOST_ENABLED = "1"
$env:AUTOCAD_MCP_ALLOW_FULL_COMPAT_FALLBACK = "0"
$env:AUTOCAD_MCP_LT_RUNTIME_ENABLED = "1"

# Current CAD Program/runtime capabilities used by the Phase 8-10 stack.
$env:AUTOCAD_MCP_PROGRAM_V0_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED = "1"
$env:AUTOCAD_MCP_CHECKPOINT_V2_ENABLED = "1"
$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_LT_WRITE_ENABLED = "0"
$env:AUTOCAD_AGENT_WRITE_LOCK_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS = if ($EnableManagedWrite) { $AllowedDeviceId } else { "" }
$env:AUTOCAD_MCP_PROGRAM_POLICY_VERSION = $PolicyVersion
$env:AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST = if ($EnableManagedWrite) { $OperationPackAllowlist } else { "" }
$env:AUTOCAD_MCP_ROLLOUT_POLICY_EPOCH = if ($EnableManagedWrite) { "1" } else { "0" }

$legacyRunner = Join-Path $PSScriptRoot "run-phase5-agent.ps1"
if (-not (Test-Path -LiteralPath $legacyRunner -PathType Leaf)) {
    throw "Current Agent runtime launcher is missing: $legacyRunner"
}

$arguments = @{}
if ($Headless) { $arguments.Headless = $true }
if ($PairOnly) { $arguments.PairOnly = $true }
if ($Source) { $arguments.Source = $true }
if (-not [string]::IsNullOrWhiteSpace($ConfigPath)) {
    $arguments.ConfigPath = $ConfigPath
}
if (-not [string]::IsNullOrWhiteSpace($AgentExe)) {
    $arguments.AgentExe = $AgentExe
}

Write-Host "Desktop Agent profile: latest managed R25 runtime" -ForegroundColor Cyan
Write-Host "Managed write: $(if ($EnableManagedWrite) { 'enabled' } else { 'disabled' })" -ForegroundColor Yellow
& $legacyRunner @arguments
exit $LASTEXITCODE
