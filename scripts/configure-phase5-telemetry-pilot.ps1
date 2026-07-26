[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ListenHost,
    [Parameter(Mandatory = $true)]
    [string]$VmSubnet,
    [string]$StateRoot = "D:\AutoCAD-MCP-Telemetry",
    [int]$Port = 4319,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "phase5-security-helpers.ps1")
$address = [System.Net.IPAddress]::Parse($ListenHost)
if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw "ListenHost must be a private IPv4 address on the Hyper-V adapter."
}
$octets = $address.GetAddressBytes()
$isPrivate = (
    $octets[0] -eq 10 -or
    ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
    ($octets[0] -eq 192 -and $octets[1] -eq 168)
)
if (-not $isPrivate) {
    throw "ListenHost must be a private IPv4 address."
}
if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "Port must be between 1024 and 65535."
}

$resolvedState = [System.IO.Path]::GetFullPath($StateRoot)
$driveRoot = [System.IO.Path]::GetPathRoot($resolvedState)
if ($resolvedState.TrimEnd("\") -eq $driveRoot.TrimEnd("\")) {
    throw "StateRoot must not be a drive root."
}
$processStatePath = Join-Path $resolvedState "collector-process.json"
if (Test-Path -LiteralPath $processStatePath -PathType Leaf) {
    $processState = Get-Content -LiteralPath $processStatePath -Raw | ConvertFrom-Json
    $collectorPid = 0
    if (
        $processState.schema -eq "autocad-mcp.telemetry-process/1" -and
        [int]::TryParse([string]$processState.pid, [ref]$collectorPid) -and
        (Get-Process -Id $collectorPid -ErrorAction SilentlyContinue)
    ) {
        throw "Telemetry collector is already running. Stop it before rotating its token."
    }
}
Protect-Phase5StateDirectory -LiteralPath $resolvedState
$configPath = Join-Path $resolvedState "collector.json"
$tokenPath = Join-Path $resolvedState "ingest-token.dpapi"
$tokenBytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($tokenBytes)
$ingestToken = [Convert]::ToBase64String($tokenBytes)
$tokenHashBytes = [Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes($ingestToken)
)
$tokenHash = [Convert]::ToHexString($tokenHashBytes).ToLowerInvariant()
$protectedToken = [Security.Cryptography.ProtectedData]::Protect(
    [Text.Encoding]::UTF8.GetBytes($ingestToken),
    $null,
    [Security.Cryptography.DataProtectionScope]::CurrentUser
)
Write-Phase5RestrictedBytes -LiteralPath $tokenPath -Bytes $protectedToken
$configText = [ordered]@{
    bind_host = $ListenHost
    port = $Port
    data_path = (Join-Path $resolvedState "aggregate.json")
    max_series = 256
    ingest_token_sha256 = $tokenHash
} | ConvertTo-Json
Write-Phase5RestrictedText `
    -LiteralPath $configPath `
    -Text ($configText + [Environment]::NewLine)

$ruleName = "AutoCAD MCP Phase5 Telemetry VM Pilot"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Remove-NetFirewallRule
}
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $ListenHost `
    -LocalPort $Port `
    -RemoteAddress $VmSubnet `
    -Profile Any | Out-Null

& (Join-Path $PSScriptRoot "start-phase5-telemetry.ps1") `
    -ConfigPath $configPath `
    -PythonExe $PythonExe

Write-Host ""
Write-Host "VM Agent setting:"
Write-Host "AUTOCAD_MCP_TELEMETRY_ENABLED=1"
Write-Host "AUTOCAD_MCP_TELEMETRY_ENDPOINT=http://${ListenHost}:${Port}/ingest/autocad-mcp"
Write-Host "Protected token: $tokenPath"
Write-Host "Dashboard on host: http://${ListenHost}:${Port}/dashboard"
