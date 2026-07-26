[CmdletBinding()]
param(
    [string]$PublicBaseUrl = "https://cad.kythuatvang.com",
    [string]$OAuthIssuer = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/",
    [string]$OAuthAudience = "https://cad.kythuatvang.com/mcp",
    [string]$OAuthJwksUri = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json",
    [string]$DatabasePath = (Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADGateway\phase5-identity.sqlite3"),
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$gatewayRoot = Join-Path $repoRoot "services\gateway"

foreach ($value in @($PublicBaseUrl, $OAuthIssuer, $OAuthJwksUri)) {
    $uri = [Uri]$value
    if ($uri.Scheme -ne "https") {
        throw "Phase 5 public OAuth URLs must use HTTPS."
    }
}
$publicUri = [Uri]$PublicBaseUrl
if ($publicUri.AbsolutePath -ne "/" -or $publicUri.Query -or $publicUri.Fragment) {
    throw "PublicBaseUrl must be an HTTPS origin without a path."
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DatabasePath) | Out-Null

$env:AUTOCAD_MCP_GATEWAY_PROFILE = "phase5_identity"
$env:AUTOCAD_MCP_PUBLIC_V1_HOST = "127.0.0.1"
$env:AUTOCAD_MCP_PUBLIC_V1_PORT = [string]$Port
$env:AUTOCAD_MCP_PUBLIC_V1_PATH = "/mcp"
$env:AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP = "0"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS = "$($publicUri.Host);127.0.0.1:$Port;localhost:$Port"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_ORIGINS = $PublicBaseUrl.TrimEnd("/")
$env:AUTOCAD_MCP_PHASE5_DB_PATH = [IO.Path]::GetFullPath($DatabasePath)
$env:AUTOCAD_MCP_PHASE3_FIXTURE_TOKENS = ""
$env:AUTOCAD_MCP_PHASE4_OAUTH_ISSUER = $OAuthIssuer
$env:AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE = $OAuthAudience
$env:AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI = $OAuthJwksUri
$env:AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN = $PublicBaseUrl.TrimEnd("/")
$env:AUTOCAD_MCP_PHASE4_WRITE_DISABLED = "1"

Write-Host "Phase 5 identity Gateway: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Public MCP: $($PublicBaseUrl.TrimEnd('/'))/mcp" -ForegroundColor Green
Write-Host "CAD write and arbitrary code: disabled" -ForegroundColor Yellow
& uv run --project $gatewayRoot --no-sync python -m autocad_gateway
exit $LASTEXITCODE
