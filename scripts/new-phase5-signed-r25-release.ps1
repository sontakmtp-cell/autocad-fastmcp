[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$UnsignedBundlePath,
    [Parameter(Mandatory)]
    [string]$CertificateThumbprint,
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$ReleaseVersion,
    [string]$OutputPath,
    [string]$TimestampServer,
    [switch]$LabOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist"))
$sourceBundle = [System.IO.Path]::GetFullPath($UnsignedBundlePath)
if (-not (Test-Path -LiteralPath $sourceBundle -PathType Container)) {
    throw "Unsigned R25 bundle was not found."
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $distRoot "phase5-signed-r25-$ReleaseVersion"
}
$releaseRoot = [System.IO.Path]::GetFullPath($OutputPath)
if (-not $releaseRoot.StartsWith(
        $distRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Signed release output must remain below repository dist."
}

$thumbprint = $CertificateThumbprint.Replace(" ", "").ToUpperInvariant()
if ($thumbprint -notmatch '^[0-9A-F]{40,64}$') {
    throw "Certificate thumbprint is invalid."
}
$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction Stop
if (-not $certificate.HasPrivateKey) {
    throw "Code-signing certificate has no private key."
}
$codeSigningOid = "1.3.6.1.5.5.7.3.3"
$ekuOids = @($certificate.EnhancedKeyUsageList | ForEach-Object {
    if ($_.ObjectId -is [string]) { $_.ObjectId } else { $_.ObjectId.Value }
})
if ($codeSigningOid -notin $ekuOids) {
    throw "Certificate is not valid for code signing."
}
if ($certificate.NotAfter -le (Get-Date).AddDays(1)) {
    throw "Code-signing certificate expires too soon."
}
if (-not $LabOnly -and [string]::IsNullOrWhiteSpace($TimestampServer)) {
    throw "Production signing requires a timestamp server."
}
if ($TimestampServer -and $TimestampServer -notmatch '^http://[A-Za-z0-9.-]+(?::\d+)?(?:/.*)?$') {
    throw "Timestamp server must be an explicit HTTP URL supported by Authenticode."
}

if (Test-Path -LiteralPath $releaseRoot) {
    Remove-Item -LiteralPath $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$bundleName = "AutocadMcp.ManagedHost.R25.bundle"
$bundleRoot = Join-Path $releaseRoot $bundleName
Copy-Item -LiteralPath $sourceBundle -Destination $bundleRoot -Recurse

$signableDlls = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Filter "*.dll"
if ($signableDlls.Count -lt 2) {
    throw "Expected Managed Host assemblies are missing."
}
$signParameters = @{
    Certificate = $certificate
    HashAlgorithm = "SHA256"
    IncludeChain = "All"
}
if ($TimestampServer) {
    $signParameters.TimestampServer = $TimestampServer
}
foreach ($file in $signableDlls) {
    $signature = Set-AuthenticodeSignature -LiteralPath $file.FullName @signParameters
    if ($signature.SignerCertificate.Thumbprint -ne $thumbprint) {
        throw "Assembly signer mismatch: $($file.Name)"
    }
    if (-not $LabOnly -and ($signature.Status -ne "Valid" -or -not $signature.TimeStamperCertificate)) {
        throw "Production assembly signature or timestamp is invalid: $($file.Name)"
    }
}

$packageManifestPath = Join-Path $bundleRoot "Contents\Shared\package-manifest.json"
$packageManifest = Get-Content -LiteralPath $packageManifestPath -Raw | ConvertFrom-Json -AsHashtable
$artifactFiles = Get-ChildItem -LiteralPath (Join-Path $bundleRoot "Contents\R25") -File |
    Sort-Object Name
$artifactHashes = [ordered]@{}
foreach ($artifact in $artifactFiles) {
    $artifactHashes[$artifact.Name] =
        (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$aggregateText = ($artifactHashes.GetEnumerator() |
    ForEach-Object { "$($_.Key):$($_.Value)" }) -join "`n"
$aggregateHash = [System.Convert]::ToHexString(
    [System.Security.Cryptography.SHA256]::HashData(
        [System.Text.Encoding]::UTF8.GetBytes($aggregateText))).ToLowerInvariant()
$packageManifest.package_version = $ReleaseVersion
$packageManifest.signed = $true
$packageManifest.package_hash = "sha256:$aggregateHash"
$packageManifest.artifacts = $artifactHashes
$packageManifest.signing = [ordered]@{
    authenticode = $true
    lab_only = [bool]$LabOnly
    certificate_thumbprint = $thumbprint
    certificate_subject = $certificate.Subject
    certificate_not_after = $certificate.NotAfter.ToUniversalTime().ToString("O")
    timestamped = [bool]$TimestampServer
}
$packageManifest | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath $packageManifestPath -Encoding utf8NoBOM

$artifacts = @()
foreach ($file in Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Sort-Object FullName) {
    $relative = [System.IO.Path]::GetRelativePath($releaseRoot, $file.FullName).Replace("\", "/")
    $requiresSignature = $file.Extension -eq ".dll"
    $signature = if ($requiresSignature) {
        Get-AuthenticodeSignature -LiteralPath $file.FullName
    } else {
        $null
    }
    if ($requiresSignature -and $signature.SignerCertificate.Thumbprint -ne $thumbprint) {
        throw "Signed artifact verification failed: $relative"
    }
    $artifacts += [ordered]@{
        path = $relative
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        authenticode_required = $requiresSignature
        signer_thumbprint = if ($requiresSignature) { $thumbprint } else { $null }
    }
}
$releaseManifest = [ordered]@{
    schema = "autocad-mcp.signed-release/1"
    release_version = $ReleaseVersion
    host_family = "R25"
    bundle_name = $bundleName
    lab_only = [bool]$LabOnly
    created_at = [DateTimeOffset]::UtcNow.ToString("O")
    signing = [ordered]@{
        certificate_thumbprint = $thumbprint
        certificate_subject = $certificate.Subject
        certificate_not_after = $certificate.NotAfter.ToUniversalTime().ToString("O")
        timestamped = [bool]$TimestampServer
    }
    artifacts = $artifacts
}
$releaseManifestPath = Join-Path $releaseRoot "release-manifest.json"
$releaseManifest | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath $releaseManifestPath -Encoding utf8NoBOM
$releaseManifestHash =
    (Get-FileHash -LiteralPath $releaseManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()

$installTemplate = Get-Content -LiteralPath (
    Join-Path $PSScriptRoot "install-phase5-signed-r25.ps1") -Raw
if ($installTemplate -notmatch '__PHASE5_RELEASE_MANIFEST_SHA256__') {
    throw "Signed installer template placeholder is missing."
}
$installPath = Join-Path $releaseRoot "Install-Phase5R25.ps1"
$installTemplate.Replace("__PHASE5_RELEASE_MANIFEST_SHA256__", $releaseManifestHash) |
    Set-Content -LiteralPath $installPath -Encoding utf8NoBOM
$rollbackPath = Join-Path $releaseRoot "Rollback-Phase5R25.ps1"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "rollback-phase5-signed-r25.ps1") `
    -Destination $rollbackPath

foreach ($scriptPath in @($installPath, $rollbackPath)) {
    $signature = Set-AuthenticodeSignature -LiteralPath $scriptPath @signParameters
    if ($signature.SignerCertificate.Thumbprint -ne $thumbprint) {
        throw "Installer script signer mismatch: $([System.IO.Path]::GetFileName($scriptPath))"
    }
    if (-not $LabOnly -and ($signature.Status -ne "Valid" -or -not $signature.TimeStamperCertificate)) {
        throw "Production installer script signature or timestamp is invalid."
    }
}

$scriptEvidence = foreach ($scriptPath in @($installPath, $rollbackPath)) {
    $signature = Get-AuthenticodeSignature -LiteralPath $scriptPath
    [ordered]@{
        path = [System.IO.Path]::GetFileName($scriptPath)
        sha256 = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
        status = $signature.Status.ToString()
        signer_thumbprint = $signature.SignerCertificate.Thumbprint
        timestamped = [bool]$signature.TimeStamperCertificate
    }
}
$evidence = [ordered]@{
    schema = "autocad-mcp.signature-evidence/1"
    lab_only = [bool]$LabOnly
    release_manifest_sha256 = $releaseManifestHash
    certificate_thumbprint = $thumbprint
    scripts = @($scriptEvidence)
}
$evidence | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath (Join-Path $releaseRoot "signature-evidence.json") -Encoding utf8NoBOM

Write-Host "Signed R25 release created: $releaseRoot"
[pscustomobject]@{
    release_root = $releaseRoot
    release_version = $ReleaseVersion
    lab_only = [bool]$LabOnly
    certificate_thumbprint = $thumbprint
    release_manifest_sha256 = $releaseManifestHash
}
