[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OAuthClientId,
    [string]$PublicOrigin = "https://cad.kythuatvang.com",
    [string]$GatewayBaseUrl = "http://127.0.0.1:8765",
    [string]$OAuthIssuer = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/",
    [string]$OAuthAudience = "https://cad.kythuatvang.com/mcp"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "phase5-security-helpers.ps1")
foreach ($value in @($PublicOrigin, $OAuthIssuer)) {
    $uri = [Uri]$value
    if ($uri.Scheme -ne "https") {
        throw "Portal public origin and OAuth issuer must use HTTPS."
    }
}
$gatewayUri = [Uri]$GatewayBaseUrl
if (
    $gatewayUri.Scheme -ne "https" -and
    -not (
        $gatewayUri.Scheme -eq "http" -and
        $gatewayUri.Host -in @("127.0.0.1", "localhost", "::1")
    )
) {
    throw "GatewayBaseUrl must use HTTPS or exact loopback HTTP."
}
$portalRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "apps\web_portal"
$target = Join-Path $portalRoot ".env.local"
$random = [byte[]]::new(48)
[Security.Cryptography.RandomNumberGenerator]::Fill($random)
$sessionSecret = [Convert]::ToBase64String($random)

$lines = @(
    "PORTAL_PUBLIC_ORIGIN=$($PublicOrigin.TrimEnd('/'))",
    "PORTAL_GATEWAY_BASE_URL=$($GatewayBaseUrl.TrimEnd('/'))",
    "PORTAL_SESSION_SECRET=$sessionSecret",
    "PORTAL_OIDC_ISSUER=$OAuthIssuer",
    "PORTAL_OIDC_CLIENT_ID=$OAuthClientId",
    "PORTAL_OIDC_CLIENT_SECRET=",
    "PORTAL_OIDC_AUDIENCE=$OAuthAudience",
    "PORTAL_OIDC_SCOPES=openid profile email autocad.read autocad.device.manage"
)
Write-Phase5RestrictedText `
    -LiteralPath $target `
    -Text (($lines -join [Environment]::NewLine) + [Environment]::NewLine)
Write-Host "Đã tạo cấu hình Portal tại apps\web_portal\.env.local" -ForegroundColor Green
Write-Host "File này bị Git bỏ qua và không được đưa vào commit."
