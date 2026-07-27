[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$BundlePath,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ($Build) {
    & (Join-Path $PSScriptRoot "build-phase6-managed-host.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 6 Managed Host build failed."
    }
}
if ([string]::IsNullOrWhiteSpace($BundlePath)) {
    $BundlePath = Join-Path $repoRoot (
        "dist\phase6-managed-host\AutocadMcp.ManagedHost.R25.bundle"
    )
}
$resolvedBundle = [System.IO.Path]::GetFullPath($BundlePath)
if (-not (Test-Path -LiteralPath $resolvedBundle -PathType Container)) {
    throw "Built bundle was not found: $resolvedBundle"
}
$packageContents = Join-Path $resolvedBundle "PackageContents.xml"
$manifestPath = Join-Path $resolvedBundle "Contents\Shared\package-manifest.json"
if (-not (Test-Path -LiteralPath $packageContents -PathType Leaf) -or
    -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Phase 6 bundle metadata is incomplete."
}
[xml]$package = Get-Content -LiteralPath $packageContents
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$requirements = $package.ApplicationPackage.RuntimeRequirements
if ($package.ApplicationPackage.AppVersion -ne "0.2.0" -or
    $manifest.package_version -ne "0.2.0" -or
    $manifest.operation_registry -ne "cad.program/0.2" -or
    $manifest.write_scope -ne "create_only" -or
    $requirements.OS -ne "Win64" -or
    $requirements.SeriesMin -ne "R25.0" -or
    $requirements.SeriesMax -ne "R25.0" -or
    $requirements.Platform -match "ACADLT") {
    throw "Bundle is not the reviewed Phase 6 R25 create-only non-LT profile."
}

$pluginsRoot = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$destination = Join-Path $pluginsRoot "AutocadMcp.ManagedHost.R25.bundle"
if ($PSCmdlet.ShouldProcess(
    $destination,
    "Install unsigned Phase 6 lab bundle for current user"
)) {
    New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
    if (Test-Path -LiteralPath $destination) {
        $backup = "$destination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $destination -Destination $backup
        Write-Host "Previous bundle backed up to: $backup"
    }
    Copy-Item -LiteralPath $resolvedBundle -Destination $destination -Recurse
    Write-Warning "Installed an UNSIGNED LAB bundle. It is not production-signed."
    Write-Host "Installed bundle: $destination"
    Write-Host (
        "Restart Mechanical 2025, handle SECURELOAD if prompted, " +
        "then run AUTOCADMCPSTATUS."
    )
}
