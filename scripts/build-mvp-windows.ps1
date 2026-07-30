[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "dist\AutoCAD-AI-Connector-MVP"
}

$distTarget = [System.IO.Path]::GetFullPath($OutputPath)
Write-Host "Building AutoCAD AI Connector MVP standalone package at:" $distTarget

# Clean target if exists
if (Test-Path -LiteralPath $distTarget) {
    Remove-Item -LiteralPath $distTarget -Recurse -Force
}

New-Item -ItemType Directory -Path $distTarget -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $distTarget "agent") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $distTarget "host_bundle") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $distTarget "config") -Force | Out-Null

# 1. Copy Desktop Agent source & packaging files
$agentSource = Join-Path $repoRoot "apps\desktop_agent"
Copy-Item -LiteralPath (Join-Path $agentSource "src") -Destination (Join-Path $distTarget "agent\src") -Recurse
Copy-Item -LiteralPath (Join-Path $agentSource "pyproject.toml") -Destination (Join-Path $distTarget "agent\pyproject.toml")
if (Test-Path -LiteralPath (Join-Path $agentSource "launcher.py")) {
    Copy-Item -LiteralPath (Join-Path $agentSource "launcher.py") -Destination (Join-Path $distTarget "agent\launcher.py")
}

# 2. Copy Managed Host R25 bundle
$hostSource = Join-Path $repoRoot "dist\phase8-managed-host\AutocadMcp.ManagedHost.R25.bundle"
if (-not (Test-Path -LiteralPath $hostSource)) {
    # If phase8 bundle is not pre-built, check phase5 lab bundle
    $hostSource = Join-Path $repoRoot "dist\phase5-release\AutocadMcp.ManagedHost.bundle"
}

if (Test-Path -LiteralPath $hostSource) {
    Copy-Item -LiteralPath $hostSource -Destination (Join-Path $distTarget "host_bundle\AutocadMcp.ManagedHost.R25.bundle") -Recurse
} else {
    # Create scaffold host bundle if not built
    Write-Warning "Host bundle dist folder not pre-built. Creating MVP scaffold host bundle."
    $scaffold = Join-Path $distTarget "host_bundle\AutocadMcp.ManagedHost.R25.bundle"
    New-Item -ItemType Directory -Path (Join-Path $scaffold "Contents\R25") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $scaffold "Contents\Shared") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $scaffold "PackageContents.xml") -Value '<?xml version="1.0" encoding="utf-8"?><ApplicationPackage SchemaVersion="1.0" AppVersion="1.0.0" Name="AutoCAD AI Connector MVP Host" Description="R25 Managed Host Gateway for AutoCAD AI Connector MVP"><Components><ComponentEntry ModuleName="./Contents/R25/AutocadMcp.Host.R25.dll" /></Components></ApplicationPackage>'
}

# 3. Create default public config file
$configContent = @'
{
  "profile": "mvp-lab",
  "gateway_url": "wss://gateway.autocad-mcp.com/wss",
  "portal_url": "http://localhost:3000",
  "write_lock_default": true,
  "managed_r25_enabled": true,
  "arbitrary_code_enabled": false,
  "advanced_lisp_enabled": false
}
'@
Set-Content -LiteralPath (Join-Path $distTarget "config\config.mvp-lab.json") -Value $configContent -Encoding UTF8

# 4. Create launcher batch script
$launcherContent = @'
@echo off
title AutoCAD AI Connector MVP Desktop Agent
echo Starting AutoCAD AI Connector MVP...
set PYTHONPATH=%~dp0agent\src;%~dp0..\..\packages\contracts\src;%~dp0..\..\packages\cad_core\src;%PYTHONPATH%
python -m autocad_desktop_agent %*
if errorlevel 1 (
    echo AutoCAD AI Connector Agent exited with code %errorlevel%
    pause
)
'@
Set-Content -LiteralPath (Join-Path $distTarget "run-agent.bat") -Value $launcherContent -Encoding UTF8

# 5. Generate Release Manifest with SHA-256 hashes
$inventory = [ordered]@{}
$files = Get-ChildItem -LiteralPath $distTarget -File -Recurse | Sort-Object FullName
foreach ($file in $files) {
    $relative = $file.FullName.Substring($distTarget.Length).TrimStart('\').Replace("\", "/")
    $inventory[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}

$releaseManifest = [ordered]@{
    schema = "autocad-ai-connector.mvp-release/1"
    product_name = "AutoCAD AI Connector MVP"
    release_version = "1.0.0"
    target_platform = "Windows x64"
    supported_autocad = @("AutoCAD 2025", "AutoCAD Mechanical 2025", "AutoCAD 2026", "AutoCAD Mechanical 2026")
    lab_release = $true
    created_at = (Get-Date -Format "o")
    artifacts_count = $files.Count
    inventory = $inventory
}

$releaseManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $distTarget "release-manifest.json") -Encoding UTF8


Write-Host "AutoCAD AI Connector MVP build complete!"
Write-Host "Artifacts location:" $distTarget
