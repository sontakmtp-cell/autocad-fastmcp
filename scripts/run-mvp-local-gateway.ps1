[CmdletBinding()]
param(
    [string]$DeviceId = "device-lab",
    [string]$DeviceCredential = "mvp-local-lab-credential-2026",
    [int]$Port = 8000,
    [string]$DatabasePath = (
        Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADGateway\mvp-local.sqlite3"
    )
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$gatewayRoot = Join-Path $repoRoot "services\gateway"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DatabasePath) | Out-Null
$packageHash = (
    Get-FileHash -LiteralPath (Join-Path $repoRoot "lisp-code\mcp_dispatch.lsp") `
        -Algorithm SHA256
).Hash.ToLowerInvariant()

$env:AUTOCAD_MCP_GATEWAY_PROFILE = "phase4_c1"
$env:AUTOCAD_MCP_PUBLIC_V1_HOST = "127.0.0.1"
$env:AUTOCAD_MCP_PUBLIC_V1_PORT = [string]$Port
$env:AUTOCAD_MCP_PUBLIC_V1_PATH = "/mcp"
$env:AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP = "0"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS = "127.0.0.1:$Port;localhost:$Port"
$env:AUTOCAD_MCP_PHASE4_DB_PATH = [IO.Path]::GetFullPath($DatabasePath)
$env:AUTOCAD_MCP_PHASE4_DEVICE_ID = $DeviceId
$env:AUTOCAD_MCP_PHASE4_DEVICE_CREDENTIAL = $DeviceCredential
$env:AUTOCAD_MCP_PHASE4_OWNER_SUBJECT = "auth0|lab-owner"
$env:AUTOCAD_MCP_PHASE4_DEVICE_DISPLAY_NAME = "May AutoCAD Lab"
$env:AUTOCAD_MCP_PHASE4_WRITE_DISABLED = "1"
$env:AUTOCAD_MCP_PHASE4_OAUTH_ISSUER = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/"
$env:AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE = "https://cad.kythuatvang.com/mcp"
$env:AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI = (
    "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json"
)
$env:AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN = "https://cad.kythuatvang.com"
$env:AUTOCAD_MCP_PHASE4_PACKAGE_ID = "autocad.lisp.drawing_info"
$env:AUTOCAD_MCP_PHASE4_PACKAGE_VERSION = "3.3-c1"
$env:AUTOCAD_MCP_PHASE4_PACKAGE_SHA256 = $packageHash

Write-Host "MVP local Gateway (phase4_c1, read-only): http://127.0.0.1:$Port" `
    -ForegroundColor Cyan
Write-Host "Agent WSS: ws://127.0.0.1:$Port/agent/ws" -ForegroundColor Green
Write-Host "Lab device: $DeviceId" -ForegroundColor Yellow

& uv run --project $gatewayRoot --no-sync python -m autocad_gateway
exit $LASTEXITCODE
