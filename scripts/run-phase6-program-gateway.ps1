[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string[]]$AllowedDeviceIds,
    [string]$PublicBaseUrl = "https://cad.kythuatvang.com",
    [string]$OAuthIssuer = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/",
    [string]$OAuthAudience = "https://cad.kythuatvang.com/mcp",
    [string]$OAuthJwksUri = (
        "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json"
    ),
    [string]$DatabasePath = (
        Join-Path $env:LOCALAPPDATA (
            "Kythuatvang\AutoCADGateway\phase6-program.sqlite3"
        )
    ),
    [string]$PolicyVersion = "phase6-policy/1",
    [switch]$EnableManagedWrite,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$gatewayRoot = Join-Path $repoRoot "services\gateway"
$allowed = @(
    $AllowedDeviceIds |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }
)
if (-not $allowed) {
    throw "At least one explicit R25 lab device ID is required."
}
if ($allowed | Where-Object {
    $_ -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
}) {
    throw "AllowedDeviceIds contains an invalid device ID."
}
foreach ($value in @($PublicBaseUrl, $OAuthIssuer, $OAuthJwksUri)) {
    $uri = [Uri]$value
    if ($uri.Scheme -ne "https") {
        throw "Phase 6 public OAuth URLs must use HTTPS."
    }
}
$publicUri = [Uri]$PublicBaseUrl
if ($publicUri.AbsolutePath -ne "/" -or
    $publicUri.Query -or
    $publicUri.Fragment) {
    throw "PublicBaseUrl must be an HTTPS origin without a path."
}

New-Item -ItemType Directory -Force -Path (
    Split-Path -Parent $DatabasePath
) | Out-Null

$env:AUTOCAD_MCP_GATEWAY_PROFILE = "phase6_program"
$env:AUTOCAD_MCP_PUBLIC_V1_HOST = "127.0.0.1"
$env:AUTOCAD_MCP_PUBLIC_V1_PORT = [string]$Port
$env:AUTOCAD_MCP_PUBLIC_V1_PATH = "/mcp"
$env:AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP = "0"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS = (
    "$($publicUri.Host);127.0.0.1:$Port;localhost:$Port"
)
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_ORIGINS = $PublicBaseUrl.TrimEnd("/")
$env:AUTOCAD_MCP_PHASE6_DB_PATH = [IO.Path]::GetFullPath($DatabasePath)
$env:AUTOCAD_MCP_PHASE3_FIXTURE_TOKENS = ""
$env:AUTOCAD_MCP_PHASE4_OAUTH_ISSUER = $OAuthIssuer
$env:AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE = $OAuthAudience
$env:AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI = $OAuthJwksUri
$env:AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN = $PublicBaseUrl.TrimEnd("/")
$env:AUTOCAD_MCP_PHASE4_WRITE_DISABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V0_ENABLED = "1"
$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_LT_WRITE_ENABLED = "0"
$env:AUTOCAD_MCP_HIGH_RISK_ENABLED = "0"
$env:AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS = $allowed -join ";"
$env:AUTOCAD_MCP_PHASE6_POLICY_VERSION = $PolicyVersion

Write-Host "Phase 6 Gateway: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Public MCP: $($PublicBaseUrl.TrimEnd('/'))/mcp" -ForegroundColor Green
Write-Host (
    "Managed write: " +
    $(if ($EnableManagedWrite) { "enabled, create-only R25 allowlist" } else { "off" })
) -ForegroundColor Yellow
& uv run --project $gatewayRoot --no-sync python -m autocad_gateway
exit $LASTEXITCODE
