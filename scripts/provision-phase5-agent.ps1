[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^wss://")]
    [string]$GatewayWsUrl,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^https://")]
    [string]$GatewayHttpUrl,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^https://")]
    [string]$PortalUrl,
    [string]$DeviceName = "Máy AutoCAD",
    [string]$PackageSource,
    [string]$TelemetryEndpoint = "",
    [Security.SecureString]$TelemetryToken,
    [string]$TelemetryTokenPath = "D:\AutoCAD-MCP-Telemetry\ingest-token.dpapi"
)

$ErrorActionPreference = "Stop"
if (-not ("System.Security.Cryptography.ProtectedData" -as [type])) {
    Add-Type -AssemblyName System.Security
}
if (-not $PackageSource) {
    $artifactPackage = Join-Path $PSScriptRoot "packages\autocad.lisp.drawing_info\3.3-c1\mcp_dispatch.lsp"
    $repoPackage = Join-Path (Split-Path -Parent $PSScriptRoot) "lisp-code\mcp_dispatch.lsp"
    $PackageSource = if (Test-Path -LiteralPath $artifactPackage) {
        $artifactPackage
    }
    else {
        $repoPackage
    }
}
$root = Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADAgent"
$packageDir = Join-Path $root "packages\autocad.lisp.drawing_info\3.3-c1"
$packageTarget = Join-Path $packageDir "mcp_dispatch.lsp"
$configTarget = Join-Path $root "agent-config.json"
$agentTelemetryToken = Join-Path $root "telemetry.token.dpapi"

New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
if (Test-Path -LiteralPath $configTarget -PathType Leaf) {
    $existingConfig = Get-Content -LiteralPath $configTarget -Raw | ConvertFrom-Json
    if ($existingConfig.schema -ne "autocad-mcp.phase5-agent-config/1") {
        $backupName = "agent-config.before-phase5-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
        $backupPath = Join-Path $root $backupName
        Copy-Item -LiteralPath $configTarget -Destination $backupPath
        Write-Host "Đã sao lưu cấu hình Agent cũ tại $backupPath"
    }
}
Copy-Item -LiteralPath (Resolve-Path -LiteralPath $PackageSource) -Destination $packageTarget -Force
$packageHash = (Get-FileHash -LiteralPath $packageTarget -Algorithm SHA256).Hash.ToLowerInvariant()

if ($TelemetryEndpoint) {
    if ($TelemetryToken) {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($TelemetryToken)
        try {
            $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            $protected = [Security.Cryptography.ProtectedData]::Protect(
                [Text.Encoding]::UTF8.GetBytes($plainToken),
                $null,
                [Security.Cryptography.DataProtectionScope]::CurrentUser
            )
            [IO.File]::WriteAllBytes($agentTelemetryToken, $protected)
        }
        finally {
            $plainToken = $null
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
    elseif (Test-Path -LiteralPath $TelemetryTokenPath -PathType Leaf) {
        Copy-Item -LiteralPath $TelemetryTokenPath -Destination $agentTelemetryToken -Force
    }
    else {
        throw "Telemetry endpoint requires a DPAPI-protected ingest token."
    }
}

$config = [ordered]@{
    schema = "autocad-mcp.phase5-agent-config/1"
    gateway_ws_url = $GatewayWsUrl
    gateway_http_url = $GatewayHttpUrl
    portal_url = $PortalUrl
    device_name = $DeviceName
    package_path = $packageTarget
    package_sha256 = $packageHash
    telemetry_endpoint = $TelemetryEndpoint
    telemetry_token_path = $(if ($TelemetryEndpoint) { $agentTelemetryToken } else { "" })
    telemetry_runtime_id = "autolisp_file_ipc"
    telemetry_runtime_role = "compatibility_fallback"
    telemetry_release_family = "R25"
    telemetry_release_year = 2025
}
$config | ConvertTo-Json | Set-Content -LiteralPath $configTarget -Encoding UTF8

Write-Host "Đã provision Agent tại $root" -ForegroundColor Green
Write-Host "Khóa thiết bị sẽ được tạo bằng DPAPI khi Agent chạy lần đầu."
Write-Host "Không có OAuth token hoặc device credential trong file cấu hình."
