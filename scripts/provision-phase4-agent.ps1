[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$DeviceId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^wss://')]
    [string]$GatewayWsUrl,

    [string]$DeviceName = 'Máy AutoCAD Lab',
    [string]$PackageSource
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 does not always load the DPAPI assembly automatically.
# PowerShell 7 usually has the type available already, so keep this conditional.
if (-not ('System.Security.Cryptography.ProtectedData' -as [type])) {
    Add-Type -AssemblyName System.Security -ErrorAction SilentlyContinue
}
if (-not ('System.Security.Cryptography.ProtectedData' -as [type])) {
    throw 'DPAPI assembly unavailable. Run this script on Windows with DPAPI support.'
}

if (-not $PackageSource) {
    $artifactPackage = Join-Path $PSScriptRoot 'packages\autocad.lisp.drawing_info\3.3-c1\mcp_dispatch.lsp'
    $repoPackage = Join-Path $PSScriptRoot '..\lisp-code\mcp_dispatch.lsp'
    $PackageSource = if (Test-Path -LiteralPath $artifactPackage) { $artifactPackage } else { $repoPackage }
}
$root = Join-Path $env:LOCALAPPDATA 'Kythuatvang\AutoCADAgent'
$packageDir = Join-Path $root 'packages\autocad.lisp.drawing_info\3.3-c1'
$packageTarget = Join-Path $packageDir 'mcp_dispatch.lsp'
$credentialTarget = Join-Path $root 'device.credential'
$configTarget = Join-Path $root 'agent-config.json'

New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
Copy-Item -LiteralPath (Resolve-Path -LiteralPath $PackageSource) -Destination $packageTarget -Force
$packageHash = (Get-FileHash -LiteralPath $packageTarget -Algorithm SHA256).Hash.ToLowerInvariant()

$secureCredential = Read-Host 'Nhập lab device credential (không hiển thị)' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureCredential)
try {
    $plainCredential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $bytes = [Text.Encoding]::UTF8.GetBytes($plainCredential)
    $protected = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [IO.File]::WriteAllBytes($credentialTarget, $protected)
}
finally {
    if ($plainCredential) { $plainCredential = $null }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$config = [ordered]@{
    gateway_ws_url = $GatewayWsUrl
    device_id = $DeviceId
    device_name = $DeviceName
    package_path = $packageTarget
    package_sha256 = $packageHash
    package_id = 'autocad.lisp.drawing_info'
    package_version = '3.3-c1'
}
$config | ConvertTo-Json | Set-Content -LiteralPath $configTarget -Encoding UTF8

Write-Host "Đã provision Agent tại $root"
Write-Host "Package SHA-256: $packageHash"
Write-Host 'Hãy thêm thư mục package vào AutoCAD Support File Search Path/TRUSTEDPATHS.'
