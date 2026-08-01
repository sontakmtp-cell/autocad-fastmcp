[CmdletBinding()]
param(
    [string]$DeviceId = "device-2d951d33-6fbb-49ca-ba22-95dfabd1ef78",
    [int]$Port = 8765,
    [string]$DatabasePath = (
        Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADGateway\phase6-program.sqlite3"
    )
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$gatewayRoot = Join-Path $repoRoot "services\gateway"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DatabasePath) | Out-Null

# Trusted pins khớp host bundle dang chay va manifest agent bao cao.
$hostPackageHash = "sha256:dc8a46c57a9aa437a55d3eacb66769dbbd862d78f3aa4a220c9b522a2b83dff5"
$capabilityManifestHash = "sha256:661c24d3da05ea15e0c773a9fb8128c3412805a5d9fdf9da069ec51c5c212873"
$operationRegistryHash = "sha256:1b840d43a4872322882f4443c07fb0f0b238cbb1d122cbefb4fe7e59097024a5"
$compilerPackageHash = "sha256:bfca03d31790509b0fd986efb9f0a36ea5d0f9655c7d9115e6f1a68e7ae09eae"
# Secret ky cursor scene: uu tien env, roi .env root, cuoi cung sinh ngau nhien.
$cursorSecret = $env:AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET
if ([string]::IsNullOrWhiteSpace($cursorSecret) -or $cursorSecret.Length -lt 32) {
    $rootEnv = Join-Path $repoRoot ".env"
    if (Test-Path -LiteralPath $rootEnv) {
        $line = Get-Content -LiteralPath $rootEnv |
            Where-Object { $_ -match '^AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET=' } |
            Select-Object -First 1
        if ($line) {
            $cursorSecret = $line.Substring(
                "AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET=".Length
            ).Trim()
        }
    }
}
if ([string]::IsNullOrWhiteSpace($cursorSecret) -or $cursorSecret.Length -lt 32) {
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    $bytes = New-Object byte[] 48
    $rng.GetBytes($bytes)
    $cursorSecret = [Convert]::ToBase64String($bytes)
}

$env:AUTOCAD_MCP_GATEWAY_PROFILE = "phase9_workflow"
$env:AUTOCAD_MCP_PUBLIC_V1_HOST = "127.0.0.1"
$env:AUTOCAD_MCP_PUBLIC_V1_PORT = [string]$Port
$env:AUTOCAD_MCP_PUBLIC_V1_PATH = "/mcp"
$env:AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP = "0"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS = "cad.kythuatvang.com;127.0.0.1:$Port;localhost:$Port"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_ORIGINS = "https://cad.kythuatvang.com"
$env:AUTOCAD_MCP_PHASE7_DB_PATH = [IO.Path]::GetFullPath($DatabasePath)
$env:AUTOCAD_MCP_PHASE4_OAUTH_ISSUER = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/"
$env:AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE = "https://cad.kythuatvang.com/mcp"
$env:AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI = (
    "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json"
)
$env:AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN = "https://cad.kythuatvang.com"
$env:AUTOCAD_MCP_PHASE4_WRITE_DISABLED = "1"
$env:AUTOCAD_MCP_PHASE4_DEVICE_DISPLAY_NAME = "May AutoCAD Lab"

# Phase 6 create-only + managed write
$env:AUTOCAD_MCP_PROGRAM_V0_ENABLED = "1"
$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = "1"
$env:AUTOCAD_MCP_LT_WRITE_ENABLED = "0"
$env:AUTOCAD_MCP_HIGH_RISK_ENABLED = "0"
$env:AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS = $DeviceId
$env:AUTOCAD_MCP_PHASE6_POLICY_VERSION = "phase6-policy/1"

# Phase 7 trusted approval + recovery/rollback
$env:AUTOCAD_MCP_PHASE7_C2_ENABLED = "1"
$env:AUTOCAD_MCP_TRUSTED_APPROVAL_ENABLED = "1"
$env:AUTOCAD_MCP_DEVICE_LOCAL_APPROVAL_ENABLED = "1"
$env:AUTOCAD_MCP_PORTAL_RECENT_AUTH_APPROVAL_ENABLED = "1"
$env:AUTOCAD_MCP_RECOVERY_CASES_ENABLED = "1"
$env:AUTOCAD_MCP_PUBLIC_ROLLBACK_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE6_DIRECT_COMMIT_LAB_ENABLED = "0"

# Phase 8 program v1 source/compiler + create/transform packs (host v1 live)
$env:AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_COMPILER_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_TOPOLOGY_PACK_ENABLED = "0"
$env:AUTOCAD_MCP_PROGRAM_V1_DELETE_PACK_ENABLED = "0"
$env:AUTOCAD_MCP_CHECKPOINT_V2_ENABLED = "1"
$env:AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST = (
    "compiler.core/1,create-equivalent/1,transform.exact/1"
)
$env:AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_EPOCH = "1"
$env:AUTOCAD_MCP_PHASE8_COMPILER_PACKAGE_HASH = $compilerPackageHash
$env:AUTOCAD_MCP_PHASE8_RUNTIME_ID = "managed_dotnet"
$env:AUTOCAD_MCP_PHASE8_HOST_FAMILY = "R25"
$env:AUTOCAD_MCP_PHASE8_HOST_VERSION = "2025"
$env:AUTOCAD_MCP_PHASE8_PACKAGE_ID = "autocad.managed_host.r25"
$env:AUTOCAD_MCP_PHASE8_PACKAGE_VERSION = "0.8.0"
$env:AUTOCAD_MCP_PHASE8_PACKAGE_HASH = $hostPackageHash
$env:AUTOCAD_MCP_PHASE8_CAPABILITY_MANIFEST_HASH = $capabilityManifestHash
$env:AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_VERSION = "cad.operation-registry/1"
$env:AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_HASH = $operationRegistryHash
$env:AUTOCAD_MCP_PHASE8_POLICY_VERSION = "phase8-policy/1"

# Phase 9 workflow engine + skills
$env:AUTOCAD_MCP_PHASE9_SKILL_CATALOG_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_WORKFLOW_ENGINE_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_PUBLIC_WORKFLOW_TOOLS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_CLEANUP_AUDIT_SKILL_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_AUTO_DIMENSION_SKILL_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_WRITE_WORKFLOWS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_POLICY_EPOCH = "1"

# Phase 10 scene engine + public tools/resources
$env:AUTOCAD_MCP_PHASE10_SCENE_ENGINE_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_PUBLIC_SCENE_TOOLS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_SCENE_RESOURCES_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_MECHANICAL_FEATURES_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_ANNOTATION_LINKS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_WORKFLOW_SCENE_STEPS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_PORTAL_SCENE_VIEWS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET = $cursorSecret
$env:AUTOCAD_MCP_PHASE10_SCENE_RETENTION_HOURS = "24"

Write-Host "MVP FULL Gateway (phase9_workflow + scene + workflows): " `
    -ForegroundColor Cyan
Write-Host "  Public MCP: https://cad.kythuatvang.com/mcp" -ForegroundColor Green
Write-Host "  Device: $DeviceId | Managed write + trusted approval" -ForegroundColor Yellow

& uv run --project $gatewayRoot --no-sync python -m autocad_gateway
exit $LASTEXITCODE
