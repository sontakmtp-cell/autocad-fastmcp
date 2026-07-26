[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$BundlePath,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ($Build) {
    & (Join-Path $PSScriptRoot "build-phase5-managed-host.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Managed Host build failed."
    }
}
if ([string]::IsNullOrWhiteSpace($BundlePath)) {
    $BundlePath = Join-Path $repoRoot "dist\phase5-managed-host\AutocadMcp.ManagedHost.R25.bundle"
}
$resolvedBundle = [System.IO.Path]::GetFullPath($BundlePath)
if (-not (Test-Path -LiteralPath $resolvedBundle -PathType Container)) {
    throw "Built bundle was not found: $resolvedBundle"
}
$packageContents = Join-Path $resolvedBundle "PackageContents.xml"
if (-not (Test-Path -LiteralPath $packageContents -PathType Leaf)) {
    throw "PackageContents.xml is missing from the bundle."
}
[xml]$package = Get-Content -LiteralPath $packageContents
$requirements = $package.ApplicationPackage.RuntimeRequirements
if ($requirements.OS -ne "Win64" -or
    $requirements.SeriesMin -ne "R25.0" -or
    $requirements.SeriesMax -ne "R25.0" -or
    $requirements.Platform -match "ACADLT") {
    throw "Bundle runtime requirements are not the reviewed R25 Windows x64 non-LT profile."
}

$pluginsRoot = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$destination = Join-Path $pluginsRoot "AutocadMcp.ManagedHost.R25.bundle"
if ($PSCmdlet.ShouldProcess($destination, "Install unsigned Phase 5 lab bundle for current user")) {
    New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
    if (Test-Path -LiteralPath $destination) {
        $backup = "$destination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $destination -Destination $backup
        Write-Host "Previous bundle backed up to: $backup"
    }
    Copy-Item -LiteralPath $resolvedBundle -Destination $destination -Recurse
    Write-Warning "Installed an UNSIGNED LAB bundle. It is not production-signed."
    Write-Host "Installed bundle: $destination"
    Write-Host "Restart AutoCAD Mechanical 2025, verify the trusted location/SECURELOAD prompt, then run AUTOCADMCPSTATUS."
}
