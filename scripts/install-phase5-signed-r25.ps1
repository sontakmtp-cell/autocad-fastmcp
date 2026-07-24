[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ReleaseRoot = $PSScriptRoot,
    [string]$PluginsRoot = (Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"),
    [string]$ReceiptRoot = (Join-Path $env:LOCALAPPDATA "KythuatVang\AutoCADMcp\install-receipts"),
    [switch]$LabOnly,
    [switch]$IsolatedTestRoot
)

$ErrorActionPreference = "Stop"
$expectedManifestHash = "__PHASE5_RELEASE_MANIFEST_SHA256__"
$releaseRootPath = [System.IO.Path]::GetFullPath($ReleaseRoot)
$pluginsRootPath = [System.IO.Path]::GetFullPath($PluginsRoot)
$receiptRootPath = [System.IO.Path]::GetFullPath($ReceiptRoot)
$defaultPluginsRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"))
if ($pluginsRootPath -ne $defaultPluginsRoot -and -not $IsolatedTestRoot) {
    throw "A non-default plugins root requires explicit -IsolatedTestRoot."
}
if ($pluginsRootPath -eq $defaultPluginsRoot -and $IsolatedTestRoot) {
    throw "Isolated test mode cannot target the real Autodesk plugins directory."
}

$manifestPath = Join-Path $releaseRootPath "release-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Signed release manifest is missing."
}
$actualManifestHash =
    (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualManifestHash -ne $expectedManifestHash) {
    throw "Signed installer release-manifest hash mismatch."
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable
if ($manifest.schema -ne "autocad-mcp.signed-release/1" -or
    $manifest.host_family -ne "R25" -or
    $manifest.bundle_name -ne "AutocadMcp.ManagedHost.R25.bundle") {
    throw "Signed release manifest contract is invalid."
}
if ($manifest.lab_only -and -not $LabOnly) {
    throw "A lab-signed release requires explicit -LabOnly."
}

$installerSignature = Get-AuthenticodeSignature -LiteralPath $PSCommandPath
if ($installerSignature.SignerCertificate.Thumbprint -ne
    $manifest.signing.certificate_thumbprint) {
    throw "Installer signer does not match the release manifest."
}
if (-not $manifest.lab_only -and
    ($installerSignature.Status -ne "Valid" -or
     -not $installerSignature.TimeStamperCertificate)) {
    throw "Production installer signature or timestamp is invalid."
}

function Get-Phase5BundleHash([string]$Path) {
    $items = foreach ($file in Get-ChildItem -LiteralPath $Path -Recurse -File |
        Sort-Object FullName) {
        $relative = [System.IO.Path]::GetRelativePath($Path, $file.FullName).Replace("\", "/")
        "${relative}:$((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($items -join "`n"))
    [System.Convert]::ToHexString(
        [System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}

foreach ($artifact in $manifest.artifacts) {
    $relative = [string]$artifact.path
    if ($relative.Contains("..") -or
        $relative -notmatch '^AutocadMcp\.ManagedHost\.R25\.bundle/[A-Za-z0-9._/-]+$') {
        throw "Release manifest contains an unsafe artifact path."
    }
    $path = [System.IO.Path]::GetFullPath((Join-Path $releaseRootPath $relative))
    if (-not $path.StartsWith(
            $releaseRootPath + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release artifact escaped the package root."
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Release artifact is missing: $relative"
    }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $artifact.sha256) {
        throw "Release artifact hash mismatch: $relative"
    }
    if ($artifact.authenticode_required) {
        $signature = Get-AuthenticodeSignature -LiteralPath $path
        if ($signature.SignerCertificate.Thumbprint -ne $artifact.signer_thumbprint) {
            throw "Release artifact signer mismatch: $relative"
        }
        if (-not $manifest.lab_only -and
            ($signature.Status -ne "Valid" -or -not $signature.TimeStamperCertificate)) {
            throw "Production artifact signature or timestamp is invalid: $relative"
        }
    }
}

if (-not $IsolatedTestRoot -and (Get-Process acad -ErrorAction SilentlyContinue)) {
    throw "Close AutoCAD before install or upgrade."
}
New-Item -ItemType Directory -Path $pluginsRootPath -Force | Out-Null
New-Item -ItemType Directory -Path $receiptRootPath -Force | Out-Null
$destination = Join-Path $pluginsRootPath $manifest.bundle_name
$nonce = [Guid]::NewGuid().ToString("N")
$staging = Join-Path $pluginsRootPath ".$($manifest.bundle_name).staging-$nonce"
$backup = $null
$backupBundleHash = $null
$sourceBundle = Join-Path $releaseRootPath $manifest.bundle_name
$sourceBundleHash = Get-Phase5BundleHash $sourceBundle

if ($PSCmdlet.ShouldProcess($destination, "Install signed Phase 5 R25 release")) {
    try {
        Copy-Item -LiteralPath $sourceBundle -Destination $staging -Recurse
        if (Test-Path -LiteralPath $destination) {
            $backupBundleHash = Get-Phase5BundleHash $destination
            $backup = "$destination.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$nonce"
            Move-Item -LiteralPath $destination -Destination $backup
        }
        Move-Item -LiteralPath $staging -Destination $destination
    }
    catch {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        if ($backup -and
            -not (Test-Path -LiteralPath $destination) -and
            (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $backup -Destination $destination
        }
        throw
    }

    $receipt = [ordered]@{
        schema = "autocad-mcp.install-receipt/2"
        status = "installed"
        installed_at = [DateTimeOffset]::UtcNow.ToString("O")
        release_version = $manifest.release_version
        release_manifest_sha256 = $actualManifestHash
        certificate_thumbprint = $manifest.signing.certificate_thumbprint
        plugins_root = $pluginsRootPath
        destination = $destination
        backup = $backup
        installed_bundle_hash = $sourceBundleHash
        backup_bundle_hash = $backupBundleHash
        lab_only = [bool]$manifest.lab_only
    }
    $receiptPath = Join-Path $receiptRootPath (
        "phase5-r25-{0}-{1}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $nonce)
    $receipt | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $receiptPath -Encoding utf8NoBOM
    Write-Host "Installed signed R25 release at: $destination"
    [pscustomobject]@{
        receipt_path = $receiptPath
        destination = $destination
        backup = $backup
        release_version = $manifest.release_version
    }
}
