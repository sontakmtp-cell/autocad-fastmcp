[CmdletBinding()]
param(
    [switch]$Headless,
    [switch]$PairOnly,
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADAgent\agent-config.json"),
    [string]$AgentExe = (Join-Path $PSScriptRoot "app\KythuatvangAutoCADAgent.exe")
)

$ErrorActionPreference = "Stop"
if (-not ("System.Security.Cryptography.ProtectedData" -as [type])) {
    Add-Type -AssemblyName System.Security
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Chưa có cấu hình Agent. Hãy chạy provision-phase5-agent.ps1 trước."
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.schema -ne "autocad-mcp.phase5-agent-config/1") {
    throw "Cấu hình Agent Phase 5 không hợp lệ."
}

$env:AUTOCAD_AGENT_IDENTITY_MODE = "browser_pairing"
$env:AUTOCAD_AGENT_GATEWAY_WS_URL = [string]$config.gateway_ws_url
$env:AUTOCAD_AGENT_GATEWAY_HTTP_URL = [string]$config.gateway_http_url
$env:AUTOCAD_AGENT_PORTAL_URL = [string]$config.portal_url
$env:AUTOCAD_AGENT_DEVICE_ID = ""
$env:AUTOCAD_AGENT_DEVICE_NAME = [string]$config.device_name
$env:AUTOCAD_AGENT_PACKAGE_PATH = [string]$config.package_path
$env:AUTOCAD_AGENT_PACKAGE_SHA256 = [string]$config.package_sha256
$env:AUTOCAD_MCP_TELEMETRY_ENABLED = if ($config.telemetry_endpoint) { "1" } else { "0" }
$env:AUTOCAD_MCP_TELEMETRY_ENDPOINT = [string]$config.telemetry_endpoint
$env:AUTOCAD_MCP_TELEMETRY_RUNTIME_ID = if ($config.telemetry_runtime_id) {
    [string]$config.telemetry_runtime_id
} else { "autolisp_file_ipc" }
$env:AUTOCAD_MCP_TELEMETRY_RUNTIME_ROLE = if ($config.telemetry_runtime_role) {
    [string]$config.telemetry_runtime_role
} else { "compatibility_fallback" }
$env:AUTOCAD_MCP_TELEMETRY_RELEASE_FAMILY = if ($config.telemetry_release_family) {
    [string]$config.telemetry_release_family
} else { "R25" }
$env:AUTOCAD_MCP_TELEMETRY_RELEASE_YEAR = if ($config.telemetry_release_year) {
    [string]$config.telemetry_release_year
} else { "2025" }
$env:AUTOCAD_MCP_TELEMETRY_TOKEN = ""
if ($config.telemetry_endpoint) {
    $protected = [IO.File]::ReadAllBytes([string]$config.telemetry_token_path)
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected,
        $null,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $env:AUTOCAD_MCP_TELEMETRY_TOKEN = [Text.Encoding]::UTF8.GetString($plain)
}

if (Test-Path -LiteralPath $AgentExe -PathType Leaf) {
    $arguments = @()
    if ($PairOnly) { $arguments += "--pair" }
    elseif ($Headless) { $arguments += "--headless" }
    & $AgentExe @arguments
}
else {
    $agentRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "apps\desktop_agent"
    $arguments = @("run", "--project", $agentRoot, "--no-sync", "autocad-desktop-agent")
    if ($PairOnly) { $arguments += "--pair" }
    elseif ($Headless) { $arguments += "--headless" }
    & uv @arguments
}
exit $LASTEXITCODE
