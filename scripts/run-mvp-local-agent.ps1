[CmdletBinding()]
param(
    [string]$DeviceId = "device-lab",
    [string]$DeviceCredential = "mvp-local-lab-credential-2026",
    [switch]$Headless,
    [switch]$ManagedHost
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$agentRoot = Join-Path $repoRoot "apps\desktop_agent"
$local = Join-Path $env:LOCALAPPDATA "Kythuatvang\AutoCADAgent"
$credentialFile = Join-Path $local "device.credential"

New-Item -ItemType Directory -Force -Path $local | Out-Null

# AutoCAD 2025 chi nap .NET bundle khi version trong cache khac bundle.
# Xoa record de lan mo AutoCAD ke tiep luon nap duoc Managed Host.
$loadedRecord = (
    "SOFTWARE\Autodesk\AutoCAD\R25.0\ACAD-8105:409\Loaded\" +
    "C:/Users/Admin/AppData/Roaming/Autodesk/ApplicationPlugins/" +
    "AutocadMcp.ManagedHost.R25.bundle"
)
$hive = [Microsoft.Win32.Registry]::CurrentUser
if ($hive.OpenSubKey($loadedRecord) -ne $null) {
    $hive.DeleteSubKeyTree($loadedRecord, $false)
    Write-Host "Da xoa cache nap Managed Host cua AutoCAD."
}

# GUI mode reads the DPAPI-protected credential file; headless reads env directly.
if (-not (Test-Path -LiteralPath $credentialFile -PathType Leaf)) {
    Add-Type -AssemblyName System.Security
    $bytes = [Text.Encoding]::UTF8.GetBytes($DeviceCredential)
    $protected = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [IO.File]::WriteAllBytes($credentialFile, $protected)
    Write-Host "Created lab device credential at $credentialFile"
}

$packageHash = (
    Get-FileHash -LiteralPath (Join-Path $repoRoot "lisp-code\mcp_dispatch.lsp") `
        -Algorithm SHA256
).Hash.ToLowerInvariant()

$env:AUTOCAD_AGENT_GATEWAY_WS_URL = "ws://127.0.0.1:8000/agent/ws"
$env:AUTOCAD_AGENT_GATEWAY_HTTP_URL = "http://127.0.0.1:8000"
$env:AUTOCAD_AGENT_PORTAL_URL = "http://localhost:3000"
$env:AUTOCAD_AGENT_DEVICE_ID = $DeviceId
$env:AUTOCAD_AGENT_DEVICE_NAME = "May AutoCAD Lab"
$env:AUTOCAD_AGENT_DEVICE_CREDENTIAL = $DeviceCredential
$env:AUTOCAD_AGENT_PACKAGE_SHA256 = $packageHash
$packageDir = Join-Path $local "packages\autocad.lisp.drawing_info\3.3-c1"
$packageTarget = Join-Path $packageDir "mcp_dispatch.lsp"
New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
if (-not (Test-Path -LiteralPath $packageTarget -PathType Leaf)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot "lisp-code\mcp_dispatch.lsp") `
        -Destination $packageTarget -Force
    Write-Host "Copied LISP package to $packageTarget"
}
$env:AUTOCAD_AGENT_PACKAGE_PATH = $packageTarget
if ($ManagedHost) {
    $env:AUTOCAD_MCP_RUNTIME_MODE = "managed_dotnet"
    $env:AUTOCAD_MCP_MANAGED_HOST_ENABLED = "1"
    $env:AUTOCAD_MCP_ALLOW_FULL_COMPAT_FALLBACK = "0"
}
else {
    $env:AUTOCAD_MCP_RUNTIME_MODE = "autolisp_compat"
    $env:AUTOCAD_MCP_MANAGED_HOST_ENABLED = "0"
    $env:AUTOCAD_MCP_ALLOW_FULL_COMPAT_FALLBACK = "1"
}
$env:AUTOCAD_MCP_LT_RUNTIME_ENABLED = "1"
$env:AUTOCAD_MCP_PROGRAM_V0_ENABLED = "1"
$env:AUTOCAD_MCP_MANAGED_WRITE_ENABLED = "0"
$env:AUTOCAD_MCP_LT_WRITE_ENABLED = "0"
$env:AUTOCAD_AGENT_WRITE_LOCK_ENABLED = "0"
$env:AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS = $DeviceId
$env:AUTOCAD_MCP_PROGRAM_POLICY_VERSION = "phase6-policy/1"

$python = Join-Path $agentRoot ".venv\Scripts\python.exe"
if ($Headless) {
    & $python -m autocad_desktop_agent --headless
}
else {
    & $python -m autocad_desktop_agent
}
exit $LASTEXITCODE
