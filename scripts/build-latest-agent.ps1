[CmdletBinding()]
param(
    [string]$OutputRoot = (Join-Path $PSScriptRoot "..\dist\latest-agent"),
    [string]$PythonVersion = "3.12",
    [switch]$SkipSync,
    [switch]$ReuseCompiledStandalone,
    [ValidateSet("auto", "msvc", "mingw64")]
    [string]$Compiler = "mingw64"
)

$ErrorActionPreference = "Stop"

# The Phase 5 builder is the current standalone packaging pipeline despite its
# legacy name. Keep it as the implementation detail and expose this stable
# phase-neutral entrypoint to operators.
$legacyBuilder = Join-Path $PSScriptRoot "build-phase5-agent.ps1"
& $legacyBuilder `
    -OutputRoot $OutputRoot `
    -PythonVersion $PythonVersion `
    -Compiler $Compiler `
    -SkipSync:$SkipSync `
    -ReuseCompiledStandalone:$ReuseCompiledStandalone
if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$resolvedOutput = [IO.Path]::GetFullPath($OutputRoot)
$canonicalRunner = Join-Path $PSScriptRoot "run-latest-agent.ps1"
if (Test-Path -LiteralPath $canonicalRunner -PathType Leaf) {
    Copy-Item -LiteralPath $canonicalRunner `
        -Destination $resolvedOutput -Force
}

$agentExe = Join-Path $resolvedOutput "app\KythuatvangAutoCADAgent.exe"
if (-not (Test-Path -LiteralPath $agentExe -PathType Leaf)) {
    throw "Latest Agent build completed without the standalone executable: $agentExe"
}

Write-Host "Latest Desktop Agent artifact: $resolvedOutput" -ForegroundColor Green
Write-Host "Canonical launcher: $(Join-Path $resolvedOutput 'run-latest-agent.ps1')" -ForegroundColor Cyan
