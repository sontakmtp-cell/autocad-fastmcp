param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "phase5-security-helpers.ps1")
$repoRoot = Split-Path -Parent $PSScriptRoot
$collector = Join-Path $repoRoot "deploy\telemetry\collector.py"
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -LiteralPath $resolvedConfig -Raw | ConvertFrom-Json
$dataPath = [System.IO.Path]::GetFullPath([string]$config.data_path)
$stateRoot = Split-Path -Parent $dataPath
Protect-Phase5StateDirectory -LiteralPath $stateRoot
$processStatePath = Join-Path $stateRoot "collector-process.json"
$outputPath = Join-Path $stateRoot "collector.out"
$errorPath = Join-Path $stateRoot "collector.err"

if (Test-Path -LiteralPath $processStatePath) {
    Assert-Phase5PathNotReparse -LiteralPath $processStatePath
    $existingPid = [int](Get-Content -LiteralPath $processStatePath -Raw | ConvertFrom-Json).pid
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        throw "Telemetry collector is already running with PID $existingPid."
    }
}

$process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList @($collector, "--config", $resolvedConfig) `
    -RedirectStandardOutput $outputPath `
    -RedirectStandardError $errorPath `
    -WindowStyle Hidden `
    -PassThru

$process.Refresh()
$processState = [ordered]@{
    schema = "autocad-mcp.telemetry-process/1"
    pid = $process.Id
    start_time_utc = $process.StartTime.ToUniversalTime().ToString("o")
    executable = $process.Path
    collector = (Resolve-Path -LiteralPath $collector).Path
} | ConvertTo-Json
Write-Phase5RestrictedText `
    -LiteralPath $processStatePath `
    -Text ($processState + [Environment]::NewLine)
Write-Host "Telemetry collector started. PID: $($process.Id)"
Write-Host "Health: http://$($config.bind_host):$($config.port)/health"
Write-Host "Dashboard: http://$($config.bind_host):$($config.port)/dashboard"
