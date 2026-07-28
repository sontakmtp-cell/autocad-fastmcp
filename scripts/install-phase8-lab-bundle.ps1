[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$BundlePath
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($BundlePath)) {
    $BundlePath = Join-Path $repoRoot (
        "dist\phase8-managed-host\AutocadMcp.ManagedHost.R25.bundle"
    )
}
$resolvedBundle = [System.IO.Path]::GetFullPath($BundlePath)
if (-not (Test-Path -LiteralPath $resolvedBundle -PathType Container)) {
    throw "Built Phase 8 bundle was not found: $resolvedBundle"
}
$packageContents = Join-Path $resolvedBundle "PackageContents.xml"
$manifestPath = Join-Path $resolvedBundle "Contents\Shared\package-manifest.json"
if (-not (Test-Path -LiteralPath $packageContents -PathType Leaf) -or
    -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Phase 8 bundle metadata is incomplete."
}
[xml]$package = Get-Content -LiteralPath $packageContents
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$requirements = $package.ApplicationPackage.RuntimeRequirements
if ($package.ApplicationPackage.AppVersion -ne "0.8.0" -or
    $manifest.package_version -ne "0.8.0" -or
    $manifest.operation_registry -ne "cad.operation-registry/1" -or
    $manifest.phase8_scope -notcontains "exact-transform" -or
    $manifest.phase8_scope -notcontains "checkpoint-v2" -or
    $manifest.extension_packs_enabled.Count -ne 0 -or
    $requirements.OS -ne "Win64" -or
    $requirements.SeriesMin -ne "R25.0" -or
    $requirements.SeriesMax -ne "R25.0" -or
    $requirements.Platform -match "ACADLT") {
    throw "Bundle is not the reviewed Phase 8 R25 lab profile."
}

$pluginsRoot = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$destination = Join-Path $pluginsRoot "AutocadMcp.ManagedHost.R25.bundle"
$resolvedPluginsRoot = [System.IO.Path]::GetFullPath($pluginsRoot)
$resolvedDestination = [System.IO.Path]::GetFullPath($destination)
if (-not $resolvedDestination.StartsWith(
    $resolvedPluginsRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to install outside the current-user ApplicationPlugins directory."
}

if ($PSCmdlet.ShouldProcess(
    $resolvedDestination,
    "Install unsigned Phase 8 lab bundle for current user"
)) {
    New-Item -ItemType Directory -Path $resolvedPluginsRoot -Force | Out-Null
    if (Test-Path -LiteralPath $resolvedDestination) {
        $backup = "$resolvedDestination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $resolvedDestination -Destination $backup
        Write-Host "Previous bundle backed up to: $backup"
    }
    Copy-Item -LiteralPath $resolvedBundle -Destination $resolvedDestination -Recurse
    Write-Warning "Installed an UNSIGNED Phase 8 LAB bundle."
    Write-Host "Installed bundle: $resolvedDestination"
    Write-Host "Restart Mechanical 2025 and run AUTOCADMCPSTATUS."
}
