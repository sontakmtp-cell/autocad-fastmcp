[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$FamilyArtifactsRoot,
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$packagingRoot = Join-Path $repoRoot "native\autocad_managed_host\packaging"
$sourceRoot = [System.IO.Path]::GetFullPath($FamilyArtifactsRoot)
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "dist\phase5-release\AutocadMcp.ManagedHost.bundle"
}
$bundleRoot = [System.IO.Path]::GetFullPath($OutputPath)
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist"))
if (-not $bundleRoot.StartsWith($distRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release-family lab output must remain under the repository dist directory."
}

& python (Join-Path $PSScriptRoot "validate-phase5-release.py")
if ($LASTEXITCODE -ne 0) {
    throw "Release-family metadata validation failed."
}

$families = @("R22", "R23", "R24", "R25")
foreach ($family in $families) {
    $expected = Join-Path $sourceRoot "$family\AutocadMcp.Host.$family.dll"
    if (-not (Test-Path -LiteralPath $expected -PathType Leaf)) {
        throw "Missing controlled-builder artifact: $expected"
    }
}

if (Test-Path -LiteralPath $bundleRoot) {
    Remove-Item -LiteralPath $bundleRoot -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $bundleRoot "Contents\Shared") -Force | Out-Null

$hashes = [ordered]@{}
foreach ($family in $families) {
    $source = Join-Path $sourceRoot $family
    $destination = Join-Path $bundleRoot "Contents\$family"
    Copy-Item -LiteralPath $source -Destination $destination -Recurse
    $files = Get-ChildItem -LiteralPath $destination -File -Recurse | Sort-Object FullName
    foreach ($file in $files) {
        $relative = [System.IO.Path]::GetRelativePath($bundleRoot, $file.FullName).Replace("\", "/")
        $hashes[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

Copy-Item -LiteralPath (Join-Path $packagingRoot "PackageContents.phase5.xml") `
    -Destination (Join-Path $bundleRoot "PackageContents.xml")
Copy-Item -LiteralPath (Join-Path $packagingRoot "phase5-release-families.json") `
    -Destination (Join-Path $bundleRoot "Contents\Shared\phase5-release-families.json")
Copy-Item -LiteralPath (Join-Path $packagingRoot "phase5-runtime-policy.json") `
    -Destination (Join-Path $bundleRoot "Contents\Shared\phase5-runtime-policy.json")
$hashes | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $bundleRoot "Contents\Shared\artifact-hashes.json") -Encoding utf8NoBOM

Write-Warning "Built an UNSIGNED LAB scaffold. It is not a production release or support certification."
Write-Host $bundleRoot
