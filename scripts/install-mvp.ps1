[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$PackagePath,
    [string]$InstallTarget
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if ([string]::IsNullOrWhiteSpace($PackagePath)) {
    $PackagePath = Join-Path $repoRoot "dist\AutoCAD-AI-Connector-MVP"
}

$sourcePackage = [System.IO.Path]::GetFullPath($PackagePath)
if (-not (Test-Path -LiteralPath $sourcePackage -PathType Container)) {
    throw "AutoCAD AI Connector MVP package directory not found: $sourcePackage"
}

$manifestPath = Join-Path $sourcePackage "release-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Release manifest missing from package: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne "autocad-ai-connector.mvp-release/1") {
    throw "Invalid release manifest schema in package."
}

# Verify SHA-256 hash inventory
Write-Host "Verifying package SHA-256 integrity..."
$inventory = $manifest.inventory
foreach ($prop in $inventory.PSObject.Properties) {
    $relPath = $prop.Name
    $expectedHash = $prop.Value
    $fullPath = Join-Path $sourcePackage ($relPath.Replace("/", "\"))
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Missing file specified in manifest inventory: $relPath"
    }
    $actualHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 hash mismatch for file: $relPath"
    }
}
Write-Host "[OK] Package SHA-256 verification passed cleanly."

# Determine installation locations
if ([string]::IsNullOrWhiteSpace($InstallTarget)) {
    $InstallTarget = Join-Path $env:LOCALAPPDATA "AutoCAD-AI-Connector-MVP"
}
$appInstallDir = [System.IO.Path]::GetFullPath($InstallTarget)
$pluginsRoot = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$hostPluginDir = Join-Path $pluginsRoot "AutocadMcp.ManagedHost.R25.bundle"

Write-Host "Installing AutoCAD AI Connector MVP..."
Write-Host "  Desktop Agent location: $appInstallDir"
Write-Host "  Managed Host location:  $hostPluginDir"

if ($PSCmdlet.ShouldProcess($appInstallDir, "Install AutoCAD AI Connector MVP")) {
    # 1. Desktop Agent installation
    if (Test-Path -LiteralPath $appInstallDir) {
        $backupAgent = "$appInstallDir.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Move-Item -LiteralPath $appInstallDir -Destination $backupAgent
        Write-Host "Backed up previous Agent installation to: $backupAgent"
    }
    Copy-Item -LiteralPath $sourcePackage -Destination $appInstallDir -Recurse

    # 2. Managed Host installation into Autodesk ApplicationPlugins
    New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
    $packagedHostBundle = Join-Path $sourcePackage "host_bundle\AutocadMcp.ManagedHost.R25.bundle"
    if (Test-Path -LiteralPath $packagedHostBundle) {
        if (Test-Path -LiteralPath $hostPluginDir) {
            $backupHost = "$hostPluginDir.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item -LiteralPath $hostPluginDir -Destination $backupHost
            Write-Host "Backed up previous Host bundle to: $backupHost"
        }
        Copy-Item -LiteralPath $packagedHostBundle -Destination $hostPluginDir -Recurse
        Write-Host "[OK] Managed Host R25 installed to Autodesk ApplicationPlugins."
    }

    # 3. Create Install Receipt
    $receiptDir = Join-Path $env:APPDATA "AutoCAD-AI-Connector"
    New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null
    $receiptPath = Join-Path $receiptDir "install-receipt.json"

    $receipt = [ordered]@{
        schema = "autocad-ai-connector.install-receipt/1"
        product_name = $manifest.product_name
        release_version = $manifest.release_version
        installed_at = (Get-Date -Format "o")
        agent_install_dir = $appInstallDir
        host_plugin_dir = $hostPluginDir
        lab_only = $true
        authenticode_status = "Controlled Alpha / Lab Certificate Release"
    }
    $receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Warning "=========================================================="
    Write-Warning "AutoCAD AI Connector MVP installed successfully!"
    Write-Warning "Notice: Controlled Alpha / Lab Release (Unsigned / Lab Cert)."
    Write-Warning "=========================================================="
}
