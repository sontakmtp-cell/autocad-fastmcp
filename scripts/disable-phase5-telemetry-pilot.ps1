param(
    [string]$StateRoot = "D:\AutoCAD-MCP-Telemetry"
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "stop-phase5-telemetry.ps1") -StateRoot $StateRoot
Get-NetFirewallRule `
    -DisplayName "AutoCAD MCP Phase5 Telemetry VM Pilot" `
    -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
Write-Host "Telemetry pilot firewall rule removed."
