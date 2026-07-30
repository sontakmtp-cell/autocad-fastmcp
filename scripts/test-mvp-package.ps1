[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

Write-Host "========================================================="
Write-Host "Running AutoCAD AI Connector MVP Package Validation Suite"
Write-Host "========================================================="

# 1. Run build script
$distDir = Join-Path $repoRoot "dist\AutoCAD-AI-Connector-MVP"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build-mvp-windows.ps1") -OutputPath $distDir
if ($LASTEXITCODE -ne 0) {
    throw "Build script execution failed."
}

# 2. Check manifest presence and integrity
$manifestPath = Join-Path $distDir "release-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Release manifest missing after build: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne "autocad-ai-connector.mvp-release/1") {
    throw "Manifest schema mismatch: $($manifest.schema)"
}

Write-Host "[OK] Release manifest verified. Version: $($manifest.release_version)"

# 3. Test Installation in Isolated Directory
$testInstallDir = Join-Path $env:TEMP "AutoCAD-AI-Connector-TestInstall"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install-mvp.ps1") -PackagePath $distDir -InstallTarget $testInstallDir
if ($LASTEXITCODE -ne 0) {
    throw "Install script execution failed."
}

if (-not (Test-Path -LiteralPath $testInstallDir -PathType Container)) {
    throw "Test installation directory was not created: $testInstallDir"
}

$receiptPath = Join-Path $env:APPDATA "AutoCAD-AI-Connector\install-receipt.json"
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
    throw "Install receipt missing: $receiptPath"
}
Write-Host "[OK] Installation verified cleanly at: $testInstallDir"

# 4. Test Uninstallation & Rollback Cleanup
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "uninstall-mvp.ps1") -InstallTarget $testInstallDir
if ($LASTEXITCODE -ne 0) {
    throw "Uninstall script execution failed."
}

if (Test-Path -LiteralPath $testInstallDir) {
    throw "Uninstallation failed to clean up agent directory: $testInstallDir"
}

Write-Host "========================================================="
Write-Host "[OK] ALL MVP Packaging Verification Tests PASSED SUCCESSFULLY!"
Write-Host "========================================================="
