param(
    [string]$BaseUrl = "http://127.0.0.1:4319"
)

$ErrorActionPreference = "Stop"
$health = Invoke-RestMethod -Uri "$BaseUrl/health" -Method Get -TimeoutSec 3
if ($health.status -ne "ok" -or $health.schema -ne "autocad-mcp.telemetry-health/1") {
    throw "Telemetry collector health contract is invalid."
}
Write-Host "OK - telemetry collector is healthy"
Write-Host "Dashboard: $BaseUrl/dashboard"
