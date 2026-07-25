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

function Assert-Phase5AuthenticodeSignature {
    param(
        [Parameter(Mandatory)]
        $Signature,
        [Parameter(Mandatory)]
        [string]$ExpectedThumbprint,
        [Parameter(Mandatory)]
        [string]$Context,
        [switch]$LabSignature
    )

    if (-not $Signature.SignerCertificate -or
        [string]::IsNullOrWhiteSpace(
            [string]$Signature.SignerCertificate.Thumbprint)) {
        throw "$Context has no Authenticode signer."
    }
    if ($Signature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint) {
        throw "$Context signer does not match the release manifest."
    }

    $status = [string]$Signature.Status
    $alwaysRejected = @(
        "HashMismatch",
        "NotSigned",
        "NotSupported",
        "Incompatible"
    )
    if ($status -in $alwaysRejected) {
        throw "$Context Authenticode status is unsafe: $status"
    }
    if ($LabSignature) {
        if ($status -in @("Valid", "NotTrusted")) {
            return
        }
        $untrustedRootMessage =
            "certificate chain processed.*terminated.*root certificate.*not trusted"
        if ($status -eq "UnknownError" -and
            [string]$Signature.StatusMessage -match $untrustedRootMessage) {
            return
        }
        throw "$Context lab Authenticode status is invalid: $status"
    }
    if ($status -ne "Valid" -or -not $Signature.TimeStamperCertificate) {
        throw "$Context production signature or timestamp is invalid."
    }
}

function Get-Phase5FileHash([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        -join ($hasher.ComputeHash($stream) |
            ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

function Get-Phase5BundleHash([string]$Path) {
    $root = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $rootPrefix = $root + [System.IO.Path]::DirectorySeparatorChar
    $items = foreach ($file in Get-ChildItem -LiteralPath $Path -Recurse -File |
        Sort-Object FullName) {
        $fullPath = [System.IO.Path]::GetFullPath($file.FullName)
        if (-not $fullPath.StartsWith(
                $rootPrefix,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Bundle hash path escaped the package root."
        }
        $relative = $fullPath.Substring($rootPrefix.Length).Replace("\", "/")
        "${relative}:$(Get-Phase5FileHash $file.FullName)"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($items -join "`n"))
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        -join ($hasher.ComputeHash($bytes) |
            ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hasher.Dispose()
    }
}

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
$actualManifestHash = Get-Phase5FileHash $manifestPath
if ($actualManifestHash -ne $expectedManifestHash) {
    throw "Signed installer release-manifest hash mismatch."
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne "autocad-mcp.signed-release/1" -or
    $manifest.host_family -ne "R25" -or
    $manifest.bundle_name -ne "AutocadMcp.ManagedHost.R25.bundle") {
    throw "Signed release manifest contract is invalid."
}
if ($manifest.lab_only -and -not $LabOnly) {
    throw "A lab-signed release requires explicit -LabOnly."
}

$installerSignature = Get-AuthenticodeSignature -LiteralPath $PSCommandPath
Assert-Phase5AuthenticodeSignature `
    -Signature $installerSignature `
    -ExpectedThumbprint $manifest.signing.certificate_thumbprint `
    -Context "Installer" `
    -LabSignature:$manifest.lab_only

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
    $hash = Get-Phase5FileHash $path
    if ($hash -ne $artifact.sha256) {
        throw "Release artifact hash mismatch: $relative"
    }
    if ($artifact.authenticode_required) {
        $signature = Get-AuthenticodeSignature -LiteralPath $path
        Assert-Phase5AuthenticodeSignature `
            -Signature $signature `
            -ExpectedThumbprint $artifact.signer_thumbprint `
            -Context "Release artifact ${relative}" `
            -LabSignature:$manifest.lab_only
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
        Set-Content -LiteralPath $receiptPath -Encoding UTF8
    Write-Host "Installed signed R25 release at: $destination"
    [pscustomobject]@{
        receipt_path = $receiptPath
        destination = $destination
        backup = $backup
        release_version = $manifest.release_version
    }
}
