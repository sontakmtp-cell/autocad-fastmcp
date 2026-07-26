[CmdletBinding()]
param(
    [string]$Subject = "CN=KythuatVang AutoCAD MCP Phase5 Lab",
    [int]$LifetimeDays = 30
)

$ErrorActionPreference = "Stop"
if ($LifetimeDays -lt 1 -or $LifetimeDays -gt 90) {
    throw "Lab certificate lifetime must be between 1 and 90 days."
}
if ($Subject -notmatch '^CN=[A-Za-z0-9 ._-]{1,120}$') {
    throw "Lab certificate subject is invalid."
}

$certificate = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -FriendlyName "AutoCAD MCP Phase5 Lab Signing - TEST ONLY" `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -HashAlgorithm SHA256 `
    -KeyAlgorithm RSA `
    -KeyLength 3072 `
    -KeyExportPolicy NonExportable `
    -NotAfter (Get-Date).AddDays($LifetimeDays)

[pscustomobject]@{
    lab_only = $true
    subject = $certificate.Subject
    thumbprint = $certificate.Thumbprint
    not_after = $certificate.NotAfter.ToUniversalTime().ToString("O")
    store = "Cert:\CurrentUser\My"
}
