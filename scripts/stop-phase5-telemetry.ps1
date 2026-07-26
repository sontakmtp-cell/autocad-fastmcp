param(
    [Parameter(Mandatory = $true)]
    [string]$StateRoot
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "phase5-security-helpers.ps1")
$resolvedRoot = [System.IO.Path]::GetFullPath($StateRoot)
Assert-Phase5PathNotReparse -LiteralPath $resolvedRoot
$processStatePath = Join-Path $resolvedRoot "collector-process.json"
if (-not (Test-Path -LiteralPath $processStatePath)) {
    Write-Host "Telemetry collector is not running."
    exit 0
}
Assert-Phase5PathNotReparse -LiteralPath $processStatePath
$state = Get-Content -LiteralPath $processStatePath -Raw | ConvertFrom-Json
if ($state.schema -ne "autocad-mcp.telemetry-process/1") {
    throw "Telemetry process state is invalid; refusing to stop any process."
}
$collectorPid = 0
if (-not [int]::TryParse([string]$state.pid, [ref]$collectorPid) -or $collectorPid -le 0) {
    throw "Telemetry process PID is invalid; refusing to stop any process."
}
$expectedCollector = (
    Resolve-Path -LiteralPath (
        Join-Path (Split-Path -Parent $PSScriptRoot) "deploy\telemetry\collector.py"
    )
).Path
if (
    [string]::IsNullOrWhiteSpace([string]$state.executable) -or
    [string]::IsNullOrWhiteSpace([string]$state.collector) -or
    -not [string]::Equals(
        [string]$state.collector,
        $expectedCollector,
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "Telemetry process identity is invalid; refusing to stop any process."
}
$process = Get-Process -Id $collectorPid -ErrorAction SilentlyContinue
if ($process) {
    $expectedStart = if ($state.start_time_utc -is [DateTime]) {
        $state.start_time_utc.ToUniversalTime()
    }
    else {
        [DateTimeOffset]::Parse([string]$state.start_time_utc).UtcDateTime
    }
    $actualStart = $process.StartTime.ToUniversalTime()
    $command = Get-CimInstance Win32_Process -Filter "ProcessId = $collectorPid"
    if (
        [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 1 -or
        -not [string]::Equals(
            $process.Path,
            [string]$state.executable,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not $command -or
        -not ([string]$command.CommandLine).Contains(
            $expectedCollector,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "PID now belongs to another process; refusing to stop it."
    }
    Stop-Process -Id $collectorPid
    Wait-Process -Id $collectorPid -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $processStatePath -Force
Write-Host "Telemetry collector stopped."
