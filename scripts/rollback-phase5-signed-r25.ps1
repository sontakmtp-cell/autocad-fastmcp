[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$ReceiptPath,
    [switch]$LabOnly,
    [switch]$IsolatedTestRoot
)

$ErrorActionPreference = "Stop"
$receiptFile = [System.IO.Path]::GetFullPath($ReceiptPath)
if (-not (Test-Path -LiteralPath $receiptFile -PathType Leaf)) {
    throw "Install receipt was not found."
}
$receipt = Get-Content -LiteralPath $receiptFile -Raw | ConvertFrom-Json -AsHashtable
if ($receipt.schema -ne "autocad-mcp.install-receipt/2" -or
    $receipt.status -ne "installed") {
    throw "Receipt is not an active Phase 5 installation."
}
if ($receipt.lab_only -and -not $LabOnly) {
    throw "A lab installation requires explicit -LabOnly rollback."
}
$rollbackSignature = Get-AuthenticodeSignature -LiteralPath $PSCommandPath
if ($rollbackSignature.SignerCertificate.Thumbprint -ne
    $receipt.certificate_thumbprint) {
    throw "Rollback signer does not match the install receipt."
}
if (-not $receipt.lab_only -and
    ($rollbackSignature.Status -ne "Valid" -or
     -not $rollbackSignature.TimeStamperCertificate)) {
    throw "Production rollback signature or timestamp is invalid."
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
    $receipt.rolled_back_at = [DateTimeOffset]::UtcNow.ToString("O")
    $receipt.displaced_install = $displaced
    $receipt | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $receiptFile -Encoding utf8NoBOM
    Write-Host "Rollback completed for: $destination"
    [pscustomobject]@{
        receipt_path = $receiptFile
        destination = $destination
        restored_backup = $backup
        displaced_install = $displaced
    }
}
