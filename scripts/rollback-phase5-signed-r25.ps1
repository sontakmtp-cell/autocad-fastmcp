[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$ReceiptPath,
    [switch]$LabOnly,
    [switch]$IsolatedTestRoot
)

$ErrorActionPreference = "Stop"

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
        throw "$Context signer does not match the install receipt."
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

$receiptFile = [System.IO.Path]::GetFullPath($ReceiptPath)
if (-not (Test-Path -LiteralPath $receiptFile -PathType Leaf)) {
    throw "Install receipt was not found."
}
$receipt = Get-Content -LiteralPath $receiptFile -Raw | ConvertFrom-Json
if ($receipt.schema -ne "autocad-mcp.install-receipt/2" -or
    $receipt.status -ne "installed") {
    throw "Receipt is not an active Phase 5 installation."
}
if ($receipt.lab_only -and -not $LabOnly) {
    throw "A lab installation requires explicit -LabOnly rollback."
}
$rollbackSignature = Get-AuthenticodeSignature -LiteralPath $PSCommandPath
Assert-Phase5AuthenticodeSignature `
    -Signature $rollbackSignature `
    -ExpectedThumbprint $receipt.certificate_thumbprint `
    -Context "Rollback" `
    -LabSignature:$receipt.lab_only

function Get-Phase5FileHash([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        -join ($hasher.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") })
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
        if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Bundle hash path escaped the package root."
        }
        $relative = $fullPath.Substring($rootPrefix.Length).Replace("\", "/")
        "${relative}:$(Get-Phase5FileHash $file.FullName)"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($items -join "`n"))
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        -join ($hasher.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hasher.Dispose()
    }
}
$pluginsRoot = [System.IO.Path]::GetFullPath([string]$receipt.plugins_root)
$destination = [System.IO.Path]::GetFullPath([string]$receipt.destination)
$defaultPluginsRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"))
if ($pluginsRoot -ne $defaultPluginsRoot -and -not $IsolatedTestRoot) {
    throw "A non-default plugins root requires explicit -IsolatedTestRoot."
}
if ($pluginsRoot -eq $defaultPluginsRoot -and $IsolatedTestRoot) {
    throw "Isolated rollback cannot target the real Autodesk plugins directory."
}
if (-not $destination.StartsWith(
        $pluginsRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase) -or
    [System.IO.Path]::GetFileName($destination) -ne "AutocadMcp.ManagedHost.R25.bundle") {
    throw "Receipt destination escaped the reviewed plugins root."
}
$backup = if ($receipt.backup) {
    [System.IO.Path]::GetFullPath([string]$receipt.backup)
} else {
    $null
}
if ($backup -and -not $backup.StartsWith(
        $pluginsRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Receipt backup escaped the reviewed plugins root."
}
if (-not $IsolatedTestRoot -and (Get-Process acad -ErrorAction SilentlyContinue)) {
    throw "Close AutoCAD before rollback."
}
if (-not (Test-Path -LiteralPath $destination -PathType Container)) {
    throw "Installed bundle is missing; rollback stopped without changing the backup."
}
if ((Get-Phase5BundleHash $destination) -ne $receipt.installed_bundle_hash) {
    throw "Installed bundle changed after install; automatic rollback stopped."
}
if ($backup -and -not (Test-Path -LiteralPath $backup -PathType Container)) {
    throw "Rollback backup is missing."
}
if ($backup -and
    (Get-Phase5BundleHash $backup) -ne $receipt.backup_bundle_hash) {
    throw "Rollback backup hash mismatch."
}

$nonce = [Guid]::NewGuid().ToString("N")
$displaced = "$destination.rolled-back-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$nonce"
if ($PSCmdlet.ShouldProcess($destination, "Rollback Phase 5 R25 release")) {
    Move-Item -LiteralPath $destination -Destination $displaced
    try {
        if ($backup) {
            Move-Item -LiteralPath $backup -Destination $destination
        }
    }
    catch {
        if (-not (Test-Path -LiteralPath $destination) -and
            (Test-Path -LiteralPath $displaced)) {
            Move-Item -LiteralPath $displaced -Destination $destination
        }
        throw
    }

    $receipt.status = "rolled_back"
    $rolledBackAt = [DateTimeOffset]::UtcNow.ToString("O")
    if ($receipt.PSObject.Properties['rolled_back_at']) {
        $receipt.rolled_back_at = $rolledBackAt
    }
    else {
        Add-Member -InputObject $receipt -MemberType NoteProperty `
            -Name rolled_back_at -Value $rolledBackAt
    }
    if ($receipt.PSObject.Properties['displaced_install']) {
        $receipt.displaced_install = $displaced
    }
    else {
        Add-Member -InputObject $receipt -MemberType NoteProperty `
            -Name displaced_install -Value $displaced
    }
    $receipt | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $receiptFile -Encoding UTF8
    Write-Host "Rollback completed for: $destination"
    [pscustomobject]@{
        receipt_path = $receiptFile
        destination = $destination
        restored_backup = $backup
        displaced_install = $displaced
    }
}
