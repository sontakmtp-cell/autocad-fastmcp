[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$BundlePath,
    [switch]$LabOnly
)

$ErrorActionPreference = "Stop"
if (-not $LabOnly) {
    throw "Phase 5 release-family installation requires explicit -LabOnly acknowledgement."
}
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$bundleRoot = [System.IO.Path]::GetFullPath($BundlePath)
if (-not (Test-Path -LiteralPath $bundleRoot -PathType Container)) {
    throw "Bundle was not found: $bundleRoot"
}
$shared = Join-Path $bundleRoot "Contents\Shared"
$manifest = Join-Path $shared "phase5-release-families.json"
$policy = Join-Path $shared "phase5-runtime-policy.json"
$packageXml = Join-Path $bundleRoot "PackageContents.xml"
$hashFile = Join-Path $shared "artifact-hashes.json"
foreach ($required in @($manifest, $policy, $packageXml, $hashFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Bundle metadata is incomplete: $required"
    }
}

& python (Join-Path $PSScriptRoot "validate-phase5-release.py") `
    --manifest $manifest --policy $policy --package-xml $packageXml
if ($LASTEXITCODE -ne 0) {
    throw "Bundle metadata validation failed."
}

$expectedHashes = Get-Content -LiteralPath $hashFile -Raw | ConvertFrom-Json -AsHashtable
foreach ($relative in $expectedHashes.Keys) {
    if ($relative -notmatch '^Contents/R2[2-5]/[A-Za-z0-9._/-]+$' -or $relative.Contains("..")) {
        throw "Artifact hash manifest contains an unsafe path."
    }
    $artifact = [System.IO.Path]::GetFullPath((Join-Path $bundleRoot $relative))
    if (-not $artifact.StartsWith($bundleRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Artifact escaped the reviewed bundle root."
    }
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Artifact is missing: $relative"
    }
    $actual = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expectedHashes[$relative]) {
        throw "Artifact hash mismatch: $relative"
    }
}

$pluginsRoot = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$destination = Join-Path $pluginsRoot "AutocadMcp.ManagedHost.bundle"
$receiptRoot = Join-Path $env:LOCALAPPDATA "KythuatVang\AutoCADMcp\install-receipts"
if ($PSCmdlet.ShouldProcess($destination, "Install unsigned Phase 5 release-family lab bundle")) {
    New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $receiptRoot -Force | Out-Null
    $backup = $null
    if (Test-Path -LiteralPath $destination) {
        $backup = "$destination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $destination -Destination $backup
    }
    Copy-Item -LiteralPath $bundleRoot -Destination $destination -Recurse
    $receipt = [ordered]@{
        schema = "autocad-mcp.install-receipt/1"
        installed_at = [DateTimeOffset]::UtcNow.ToString("O")
        destination = $destination
        backup = $backup
        lab_only = $true
        signed = $false
    }
    $receiptPath = Join-Path $receiptRoot ("phase5-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $receipt | ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding utf8NoBOM
    Write-Warning "Installed an UNSIGNED LAB bundle. Close AutoCAD before rollback; use the receipt's exact backup path."
    Write-Host "Installed bundle: $destination"
    Write-Host "Rollback receipt: $receiptPath"
}
