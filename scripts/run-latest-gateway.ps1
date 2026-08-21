[CmdletBinding()]
param(
    [string]$PublicBaseUrl = "https://cad.kythuatvang.com",
    [string]$OAuthIssuer = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/",
    [string]$OAuthAudience = "https://cad.kythuatvang.com/mcp",
    [string]$OAuthJwksUri = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json",
    [string]$DatabasePath = (Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADGateway\latest-workflow.sqlite3"),
    [int]$Port = 8765,
    [string[]]$AllowedDeviceIds = @(),
    [switch]$EnableManagedWrite,
    [string]$OperationPackAllowlist = $env:AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST,
    [string]$Phase8CompilerPackageHash = $env:AUTOCAD_MCP_PHASE8_COMPILER_PACKAGE_HASH,
    [string]$Phase8RuntimeId = $env:AUTOCAD_MCP_PHASE8_RUNTIME_ID,
    [string]$Phase8HostFamily = $env:AUTOCAD_MCP_PHASE8_HOST_FAMILY,
    [string]$Phase8HostVersion = $env:AUTOCAD_MCP_PHASE8_HOST_VERSION,
    [string]$Phase8PackageId = $env:AUTOCAD_MCP_PHASE8_PACKAGE_ID,
    [string]$Phase8PackageVersion = $env:AUTOCAD_MCP_PHASE8_PACKAGE_VERSION,
    [string]$Phase8PackageHash = $env:AUTOCAD_MCP_PHASE8_PACKAGE_HASH,
    [string]$Phase8CapabilityManifestHash = $env:AUTOCAD_MCP_PHASE8_CAPABILITY_MANIFEST_HASH,
    [string]$Phase8OperationRegistryVersion = $env:AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_VERSION,
    [string]$Phase8OperationRegistryHash = $env:AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_HASH,
    [string]$Phase8PolicyVersion = "phase8-policy/1",
    [int]$Phase8RolloutPolicyEpoch = 1,
    [string]$Phase8RolloutPolicyDigest = $env:AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_DIGEST,
    [string]$CursorSigningSecret = $env:AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET,
    [int]$SceneRetentionHours = 24,
    [switch]$NoAutoResolveRuntimePins
)

$ErrorActionPreference = "Stop"
# Canonical latest startup is fully configured by this launcher. Ignore any
# ambient/local .env discovered by third-party packages so stale phase config
# cannot alter or pollute the latest runtime.
$env:PYTHON_DOTENV_DISABLED = "1"
$repoRoot = Split-Path -Parent $PSScriptRoot
$gatewayRoot = Join-Path $repoRoot "services\gateway"
$agentRoot = Join-Path $repoRoot "apps\desktop_agent"

foreach ($value in @($PublicBaseUrl, $OAuthIssuer, $OAuthJwksUri)) {
    $uri = [Uri]$value
    if ($uri.Scheme -ne "https") {
        throw "Latest Gateway public OAuth URLs must use HTTPS."
    }
}
$publicUri = [Uri]$PublicBaseUrl
if ($publicUri.AbsolutePath -ne "/" -or $publicUri.Query -or $publicUri.Fragment) {
    throw "PublicBaseUrl must be an HTTPS origin without a path."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}
if ($SceneRetentionHours -lt 1 -or $SceneRetentionHours -gt 168) {
    throw "SceneRetentionHours must be between 1 and 168."
}

if ([string]::IsNullOrWhiteSpace($Phase8RuntimeId)) { $Phase8RuntimeId = "managed_dotnet" }
if ([string]::IsNullOrWhiteSpace($Phase8HostFamily)) { $Phase8HostFamily = "R25" }
if ([string]::IsNullOrWhiteSpace($Phase8HostVersion)) { $Phase8HostVersion = "2025" }
if ([string]::IsNullOrWhiteSpace($Phase8OperationRegistryVersion)) {
    $Phase8OperationRegistryVersion = "cad.program/1.0-create-core"
}

function Get-MissingRuntimePins {
    $values = @{
        "Phase8CompilerPackageHash" = $Phase8CompilerPackageHash
        "Phase8PackageId" = $Phase8PackageId
        "Phase8PackageVersion" = $Phase8PackageVersion
        "Phase8PackageHash" = $Phase8PackageHash
        "Phase8CapabilityManifestHash" = $Phase8CapabilityManifestHash
        "Phase8OperationRegistryHash" = $Phase8OperationRegistryHash
    }
    return @(
        $values.GetEnumerator() |
        Where-Object { [string]::IsNullOrWhiteSpace([string]$_.Value) } |
        ForEach-Object { $_.Key }
    )
}

$missing = @(Get-MissingRuntimePins)
if ($missing.Count -gt 0 -and -not $NoAutoResolveRuntimePins) {
    $resolver = Join-Path $PSScriptRoot "resolve-latest-runtime-pins.py"
    if (-not (Test-Path -LiteralPath $resolver -PathType Leaf)) {
        throw "Runtime pin resolver is missing: $resolver"
    }

    Write-Host "Resolving trusted runtime pins from the local Managed .NET R25 Host..." -ForegroundColor Cyan
    try {
        $pinOutput = @(
            & uv run --project $agentRoot --no-sync python $resolver 2>&1
        )
        if ($LASTEXITCODE -ne 0) {
            throw ($pinOutput -join [Environment]::NewLine)
        }
        $pinText = ($pinOutput -join [Environment]::NewLine).Trim()
        $pins = $pinText | ConvertFrom-Json
    }
    catch {
        throw (
            "Could not auto-resolve trusted Phase 8 runtime pins from the local Managed Host. " +
            "Make sure AutoCAD Mechanical 2025 is open, the current Managed R25 Host is loaded, " +
            "and the Desktop Agent environment has been built. Resolver error: " + $_.Exception.Message
        )
    }

    if ([string]::IsNullOrWhiteSpace($Phase8CompilerPackageHash)) {
        $Phase8CompilerPackageHash = [string]$pins.compiler_package_hash
    }
    if ([string]::IsNullOrWhiteSpace($Phase8RuntimeId)) {
        $Phase8RuntimeId = [string]$pins.runtime_id
    }
    if ([string]::IsNullOrWhiteSpace($Phase8HostFamily)) {
        $Phase8HostFamily = [string]$pins.host_family
    }
    if ([string]::IsNullOrWhiteSpace($Phase8HostVersion)) {
        $Phase8HostVersion = [string]$pins.host_version
    }
    if ([string]::IsNullOrWhiteSpace($Phase8PackageId)) {
        $Phase8PackageId = [string]$pins.package_id
    }
    if ([string]::IsNullOrWhiteSpace($Phase8PackageVersion)) {
        $Phase8PackageVersion = [string]$pins.package_version
    }
    if ([string]::IsNullOrWhiteSpace($Phase8PackageHash)) {
        $Phase8PackageHash = [string]$pins.package_hash
    }
    if ([string]::IsNullOrWhiteSpace($Phase8CapabilityManifestHash)) {
        $Phase8CapabilityManifestHash = [string]$pins.capability_manifest_hash
    }
    if ([string]::IsNullOrWhiteSpace($Phase8OperationRegistryVersion)) {
        $Phase8OperationRegistryVersion = [string]$pins.operation_registry_version
    }
    if ([string]::IsNullOrWhiteSpace($Phase8OperationRegistryHash)) {
        $Phase8OperationRegistryHash = [string]$pins.operation_registry_hash
    }

    $missing = @(Get-MissingRuntimePins)
}

if ($missing.Count -gt 0) {
    throw (
        "Latest Gateway requires trusted Phase 8 runtime pins. Missing: " +
        ($missing -join ", ") +
        ". Auto-resolution is unavailable or disabled; pass parameters or set the matching AUTOCAD_MCP_PHASE8_* environment variables."
    )
}

foreach ($digest in @(
    @{ Name = "Phase8CompilerPackageHash"; Value = $Phase8CompilerPackageHash },
    @{ Name = "Phase8PackageHash"; Value = $Phase8PackageHash },
    @{ Name = "Phase8CapabilityManifestHash"; Value = $Phase8CapabilityManifestHash },
    @{ Name = "Phase8OperationRegistryHash"; Value = $Phase8OperationRegistryHash }
)) {
    if ([string]$digest.Value -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "$($digest.Name) must be a canonical sha256:<64 lowercase hex> digest."
    }
}
if (-not [string]::IsNullOrWhiteSpace($Phase8RolloutPolicyDigest) -and
    $Phase8RolloutPolicyDigest -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Phase8RolloutPolicyDigest must be a canonical SHA-256 digest."
}
if ($Phase8RolloutPolicyEpoch -lt 1) {
    throw "Phase8RolloutPolicyEpoch must be at least 1 for the latest compiler profile."
}

# Local canonical startup keeps a stable cursor-signing secret across restarts.
# Production deployments may still inject AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET.
if ([string]::IsNullOrWhiteSpace($CursorSigningSecret)) {
    $secretRoot = Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADGateway"
    $secretPath = Join-Path $secretRoot "latest-scene-cursor.secret"
    New-Item -ItemType Directory -Force -Path $secretRoot | Out-Null
    if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
        $CursorSigningSecret = (Get-Content -LiteralPath $secretPath -Raw).Trim()
    }
    else {
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $bytes = New-Object byte[] 48
            $rng.GetBytes($bytes)
            $CursorSigningSecret = [Convert]::ToBase64String($bytes)
        }
        finally {
            $rng.Dispose()
        }
        Set-Content -LiteralPath $secretPath -Value $CursorSigningSecret -Encoding ASCII
        Write-Host "Created persistent local Phase 10 cursor secret: $secretPath" -ForegroundColor DarkGray
    }
}
if ([Text.Encoding]::UTF8.GetByteCount($CursorSigningSecret) -lt 32) {
    throw "Phase 10 cursor signing secret must contain at least 32 UTF-8 bytes."
}

$allowed = @(
    $AllowedDeviceIds |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ }
)
if ($allowed | Where-Object { $_ -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' }) {
    throw "AllowedDeviceIds contains an invalid device ID."
}
if ($EnableManagedWrite) {
    if (-not $allowed) {
        throw "EnableManagedWrite requires at least one AllowedDeviceIds value."
    }
    if ([string]::IsNullOrWhiteSpace($OperationPackAllowlist)) {
        throw "EnableManagedWrite requires OperationPackAllowlist."
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DatabasePath) | Out-Null

# Phase 10 is intentionally a feature layer on top of the Phase 9 workflow
# profile. There is no separate phase10 Gateway profile in the application.
$env:AUTOCAD_MCP_GATEWAY_PROFILE = "phase9_workflow"
$env:AUTOCAD_MCP_PUBLIC_V1_HOST = "127.0.0.1"
$env:AUTOCAD_MCP_PUBLIC_V1_PORT = [string]$Port
$env:AUTOCAD_MCP_PUBLIC_V1_PATH = "/mcp"
$env:AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP = "0"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS = "$($publicUri.Host);127.0.0.1:$Port;localhost:$Port"
$env:AUTOCAD_MCP_PUBLIC_V1_ALLOWED_ORIGINS = $PublicBaseUrl.TrimEnd("/")
$env:AUTOCAD_MCP_PHASE7_DB_PATH = [IO.Path]::GetFullPath($DatabasePath)
$env:AUTOCAD_MCP_PHASE3_FIXTURE_TOKENS = ""
$env:AUTOCAD_MCP_PHASE4_OAUTH_ISSUER = $OAuthIssuer
$env:AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE = $OAuthAudience
$env:AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI = $OAuthJwksUri
$env:AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN = $PublicBaseUrl.TrimEnd("/")
$env:AUTOCAD_MCP_PHASE4_WRITE_DISABLED = "1"

# Phase 6/7 safety and write gates retained by the latest stack.
$env:AUTOCAD_MCP_PROGRAM_V0_ENABLED = "1"
$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_LT_WRITE_ENABLED = "0"
$env:AUTOCAD_MCP_HIGH_RISK_ENABLED = "0"
$env:AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS = if ($EnableManagedWrite) { $allowed -join ";" } else { "" }
$env:AUTOCAD_MCP_PHASE7_C2_ENABLED = "1"
$env:AUTOCAD_MCP_TRUSTED_APPROVAL_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_DEVICE_LOCAL_APPROVAL_ENABLED = "0"
$env:AUTOCAD_MCP_PORTAL_RECENT_AUTH_APPROVAL_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PUBLIC_ROLLBACK_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_RECOVERY_CASES_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PHASE6_DIRECT_COMMIT_LAB_ENABLED = "0"

# Phase 8 compiler/runtime pins.
$env:AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_COMPILER_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V1_TOPOLOGY_PACK_ENABLED = "0"
$env:AUTOCAD_MCP_PROGRAM_V1_DELETE_PACK_ENABLED = "0"
$env:AUTOCAD_MCP_CHECKPOINT_V2_ENABLED = "1"
$env:AUTOCAD_MCP_SCOPED_ROLLBACK_REVALIDATION_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_LT_PORTABLE_WRITE_ENABLED = "0"
$env:AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST = if ($EnableManagedWrite) { $OperationPackAllowlist } else { "" }
$env:AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_EPOCH = [string]$Phase8RolloutPolicyEpoch
$env:AUTOCAD_MCP_PHASE8_ROLLOUT_POLICY_DIGEST = $Phase8RolloutPolicyDigest
$env:AUTOCAD_MCP_PHASE8_COMPILER_PACKAGE_HASH = $Phase8CompilerPackageHash
$env:AUTOCAD_MCP_PHASE8_RUNTIME_ID = $Phase8RuntimeId
$env:AUTOCAD_MCP_PHASE8_HOST_FAMILY = $Phase8HostFamily
$env:AUTOCAD_MCP_PHASE8_HOST_VERSION = $Phase8HostVersion
$env:AUTOCAD_MCP_PHASE8_PACKAGE_ID = $Phase8PackageId
$env:AUTOCAD_MCP_PHASE8_PACKAGE_VERSION = $Phase8PackageVersion
$env:AUTOCAD_MCP_PHASE8_PACKAGE_HASH = $Phase8PackageHash
$env:AUTOCAD_MCP_PHASE8_CAPABILITY_MANIFEST_HASH = $Phase8CapabilityManifestHash
$env:AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_VERSION = $Phase8OperationRegistryVersion
$env:AUTOCAD_MCP_PHASE8_OPERATION_REGISTRY_HASH = $Phase8OperationRegistryHash
$env:AUTOCAD_MCP_PHASE8_POLICY_VERSION = $Phase8PolicyVersion

# Phase 9 workflow/skill platform.
$env:AUTOCAD_MCP_PHASE9_SKILL_CATALOG_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_WORKFLOW_ENGINE_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_PUBLIC_WORKFLOW_TOOLS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_AUTO_DIMENSION_SKILL_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_CLEANUP_AUDIT_SKILL_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_PLATE_PATTERN_SKILL_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE9_WRITE_WORKFLOWS_ENABLED = if ($EnableManagedWrite) { "1" } else { "0" }
$env:AUTOCAD_MCP_PHASE9_SKILL_ALLOWLIST = "mechanical.auto-dimension-overall,drawing.cleanup-audit,mechanical.plate-hole-pattern"
$env:AUTOCAD_MCP_PHASE9_POLICY_EPOCH = "1"

# Phase 10 scene intelligence. These flags intentionally run on phase9_workflow.
$env:AUTOCAD_MCP_PHASE10_SCENE_ENGINE_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_PUBLIC_SCENE_TOOLS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_SCENE_RESOURCES_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_MECHANICAL_FEATURES_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_ANNOTATION_LINKS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_WORKFLOW_SCENE_STEPS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_PORTAL_SCENE_VIEWS_ENABLED = "1"
$env:AUTOCAD_MCP_PHASE10_CURSOR_SIGNING_SECRET = $CursorSigningSecret
$env:AUTOCAD_MCP_PHASE10_SCENE_RETENTION_HOURS = [string]$SceneRetentionHours

Write-Host "Gateway profile: latest (phase9_workflow + Phase 10 features)" -ForegroundColor Cyan
Write-Host "Runtime pins: $Phase8PackageId $Phase8PackageVersion / $Phase8RuntimeId $Phase8HostFamily $Phase8HostVersion" -ForegroundColor DarkGray
Write-Host "Public MCP: $($PublicBaseUrl.TrimEnd('/'))/mcp" -ForegroundColor Green
Write-Host "Managed write: $(if ($EnableManagedWrite) { 'enabled with trusted approval' } else { 'disabled' })" -ForegroundColor Yellow
& uv run --project $gatewayRoot --no-sync python -m autocad_gateway
exit $LASTEXITCODE
