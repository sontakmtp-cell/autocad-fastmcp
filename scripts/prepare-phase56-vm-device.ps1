#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$VMName = "Phase4-Win11-Clean",
    [string]$GuestUser = "phase4-clean\phase4lab",
    [string]$ArtifactRoot = (Join-Path $PSScriptRoot "..\dist\phase5-agent"),
    [string]$TelemetryHost = "172.20.96.1",
    [string]$TelemetryTokenPath = "D:\AutoCAD-MCP-Telemetry\ingest-token.dpapi",
    [string]$CheckpointName = "before-phase56-two-user-pilot"
)

$ErrorActionPreference = "Stop"
$resolvedArtifact = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$agentExe = Join-Path $resolvedArtifact "app\KythuatvangAutoCADAgent.exe"
if (-not (Test-Path -LiteralPath $agentExe -PathType Leaf)) {
    throw "Phase 5 Agent artifact chưa sẵn sàng: $agentExe"
}
$protectedTelemetryToken = [IO.File]::ReadAllBytes(
    (Resolve-Path -LiteralPath $TelemetryTokenPath).Path
)
$plainTelemetryToken = [Security.Cryptography.ProtectedData]::Unprotect(
    $protectedTelemetryToken,
    $null,
    [Security.Cryptography.DataProtectionScope]::CurrentUser
)
$secureTelemetryToken = ConvertTo-SecureString `
    ([Text.Encoding]::UTF8.GetString($plainTelemetryToken)) `
    -AsPlainText `
    -Force
[Array]::Clear($plainTelemetryToken, 0, $plainTelemetryToken.Length)

$vm = Get-VM -Name $VMName -ErrorAction Stop
if ($vm.State -ne "Running") {
    Start-VM -Name $VMName | Out-Null
    $vm | Wait-VM -For IPAddress -Timeout 120 | Out-Null
}
if (-not (Get-VMSnapshot -VMName $VMName -Name $CheckpointName -ErrorAction SilentlyContinue)) {
    Checkpoint-VM -Name $VMName -SnapshotName $CheckpointName
}

$credential = Get-Credential -UserName $GuestUser
$session = New-PSSession -VMName $VMName -Credential $credential
try {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $guestRoot = "C:\Phase5Pilot\phase5-agent-$stamp"
    Invoke-Command -Session $session -ScriptBlock {
        param($Path)
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    } -ArgumentList $guestRoot
    Copy-Item -ToSession $session -Path (Join-Path $resolvedArtifact "*") `
        -Destination $guestRoot -Recurse -Force
    Invoke-Command -Session $session -ScriptBlock {
        param($Path, $TelemetryHost, $TelemetryToken)
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
        Set-Location $Path
        & .\provision-phase5-agent.ps1 `
            -GatewayWsUrl "wss://cad.kythuatvang.com/agent/ws" `
            -GatewayHttpUrl "https://cad.kythuatvang.com" `
            -PortalUrl "https://cad.kythuatvang.com" `
            -DeviceName "Device B - VM phase4lab" `
            -TelemetryEndpoint "http://${TelemetryHost}:4319/ingest/autocad-mcp" `
            -TelemetryToken $TelemetryToken
    } -ArgumentList $guestRoot, $TelemetryHost, $secureTelemetryToken
}
finally {
    Remove-PSSession $session
}

Write-Host "Device B đã được copy và provision trong VM." -ForegroundColor Green
Write-Host "Mở cửa sổ VM, đăng nhập phase4lab, rồi chạy:"
Write-Host "  Set-Location '$guestRoot'"
Write-Host "  .\run-phase5-agent.ps1"
