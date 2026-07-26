[CmdletBinding()]
param(
    [string]$OutputRoot = (Join-Path $PSScriptRoot "..\dist\phase5-agent"),
    [string]$PythonVersion = "3.12",
    [switch]$SkipSync,
    [switch]$ReuseCompiledStandalone,
    [ValidateSet("auto", "msvc", "mingw64")]
    [string]$Compiler = "auto"
)

$ErrorActionPreference = "Stop"
$builder = Join-Path $PSScriptRoot "build-phase4-agent.ps1"
& $builder `
    -OutputRoot $OutputRoot `
    -PythonVersion $PythonVersion `
    -Compiler $Compiler `
    -SkipSync:$SkipSync `
    -ReuseCompiledStandalone:$ReuseCompiledStandalone
if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$resolvedOutput = [IO.Path]::GetFullPath($OutputRoot)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "provision-phase5-agent.ps1") `
    -Destination $resolvedOutput -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run-phase5-agent.ps1") `
    -Destination $resolvedOutput -Force

$manifestPath = Join-Path $resolvedOutput "manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$manifest.schema = "cad.agent.phase5-release/1"
$manifest.launch_script = "run-phase5-agent.ps1"
$manifest.provision_script = "provision-phase5-agent.ps1"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$selfTest = Start-Process `
    -FilePath (Join-Path $resolvedOutput "app\KythuatvangAutoCADAgent.exe") `
    -ArgumentList "--package-self-test" `
    -Wait `
    -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Phase 5 standalone Agent self-test failed."
}
Write-Host "Phase 5 Agent artifact: $resolvedOutput" -ForegroundColor Green
