[CmdletBinding()]
param(
    [switch]$Headless,
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA 'Kythuatvang\AutoCADAgent\agent-config.json'),
    [string]$AgentExe = ''
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($AgentExe)) {
    $AgentExe = Join-Path $PSScriptRoot 'app\KythuatvangAutoCADAgent.exe'
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Chưa có cấu hình Agent: $ConfigPath. Hãy chạy provision-phase4-agent.ps1 trước."
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$env:AUTOCAD_AGENT_GATEWAY_WS_URL = [string]$config.gateway_ws_url
$env:AUTOCAD_AGENT_DEVICE_ID = [string]$config.device_id
$env:AUTOCAD_AGENT_DEVICE_NAME = [string]$config.device_name
$env:AUTOCAD_AGENT_PACKAGE_PATH = [string]$config.package_path
$env:AUTOCAD_AGENT_PACKAGE_SHA256 = [string]$config.package_sha256

if (Test-Path -LiteralPath $AgentExe -PathType Leaf) {
    if ($Headless) { & $AgentExe --headless } else { & $AgentExe }
}
else {
    $agentRoot = Join-Path $PSScriptRoot '..\apps\desktop_agent'
    if ($Headless) {
        & uv run --project $agentRoot --no-sync autocad-desktop-agent --headless
    }
    else {
        & uv run --project $agentRoot --no-sync autocad-desktop-agent
    }
}
exit $LASTEXITCODE
