[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ReleaseV1Root,
    [Parameter(Mandatory)]
    [string]$ReleaseV2Root,
    [Parameter(Mandatory)]
    [string]$WorkRoot,
    [Parameter(Mandatory)]
    [string]$EvidencePath
)

$ErrorActionPreference = "Stop"
$workRootPath = [System.IO.Path]::GetFullPath($WorkRoot)
$evidenceFile = [System.IO.Path]::GetFullPath($EvidencePath)
if (Test-Path -LiteralPath $workRootPath) {
    throw "Clean rehearsal root must not already exist."
}
$pluginsRoot = Join-Path $workRootPath "ApplicationPlugins"
$receiptRoot = Join-Path $workRootPath "Receipts"
New-Item -ItemType Directory -Path $workRootPath -Force | Out-Null

function Get-BundleHash([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $null
    }
    $items = foreach ($file in Get-ChildItem -LiteralPath $Path -Recurse -File |
        Sort-Object FullName) {
        $relative = [System.IO.Path]::GetRelativePath($Path, $file.FullName).Replace("\", "/")
        "${relative}:$((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($items -join "`n"))
    [System.Convert]::ToHexString(
        [System.Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}

$installV1 = Join-Path $ReleaseV1Root "Install-Phase5R25.ps1"
$rollbackV1 = Join-Path $ReleaseV1Root "Rollback-Phase5R25.ps1"
$installV2 = Join-Path $ReleaseV2Root "Install-Phase5R25.ps1"
$rollbackV2 = Join-Path $ReleaseV2Root "Rollback-Phase5R25.ps1"
foreach ($script in @($installV1, $rollbackV1, $installV2, $rollbackV2)) {
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
        throw "Release script is missing: $script"
    }
}

$v1 = @(& $installV1 -ReleaseRoot $ReleaseV1Root -PluginsRoot $pluginsRoot `
    -ReceiptRoot $receiptRoot -LabOnly -IsolatedTestRoot) |
    Where-Object { $_.PSObject.Properties.Name -contains "receipt_path" } |
    Select-Object -Last 1
$destination = $v1.destination
$v1Hash = Get-BundleHash $destination
if (-not $v1Hash) {
    throw "Clean install did not create the R25 bundle."
}

$v2 = @(& $installV2 -ReleaseRoot $ReleaseV2Root -PluginsRoot $pluginsRoot `
    -ReceiptRoot $receiptRoot -LabOnly -IsolatedTestRoot) |
    Where-Object { $_.PSObject.Properties.Name -contains "receipt_path" } |
    Select-Object -Last 1
if (-not $v2.backup -or -not (Test-Path -LiteralPath $v2.backup -PathType Container)) {
    throw "Upgrade did not preserve the previous known-good bundle."
}
$v2Hash = Get-BundleHash $destination

$null = & $rollbackV2 -ReceiptPath $v2.receipt_path -LabOnly -IsolatedTestRoot
$restoredHash = Get-BundleHash $destination
if ($restoredHash -ne $v1Hash) {
    throw "Upgrade rollback did not restore the exact previous bundle."
}

$null = & $rollbackV1 -ReceiptPath $v1.receipt_path -LabOnly -IsolatedTestRoot
if (Test-Path -LiteralPath $destination) {
    throw "Clean-install rollback did not remove the installed bundle."
}

$evidence = [ordered]@{
    schema = "autocad-mcp.clean-install-rollback/1"
    created_at = [DateTimeOffset]::UtcNow.ToString("O")
    os = [System.Environment]::OSVersion.VersionString
    powershell = $PSVersionTable.PSVersion.ToString()
    work_root_was_absent = $true
    clean_install = "passed"
    upgrade = "passed"
    upgrade_rollback = "passed"
    clean_install_rollback = "passed"
    v1_bundle_hash = $v1Hash
    v2_bundle_hash = $v2Hash
    restored_v1_bundle_hash = $restoredHash
}
New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($evidenceFile)) -Force |
    Out-Null
$evidence | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $evidenceFile -Encoding utf8NoBOM
[pscustomobject]$evidence
