[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$InstallTarget
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InstallTarget)) {
    $InstallTarget = Join-Path $env:LOCALAPPDATA "AutoCAD-AI-Connector-MVP"
}

$appInstallDir = [System.IO.Path]::GetFullPath($InstallTarget)
$pluginsRoot = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$hostPluginDir = Join-Path $pluginsRoot "AutocadMcp.ManagedHost.R25.bundle"
$receiptPath = Join-Path $env:APPDATA "AutoCAD-AI-Connector\install-receipt.json"

Write-Host "Uninstalling AutoCAD AI Connector MVP..."

if ($PSCmdlet.ShouldProcess($appInstallDir, "Uninstall AutoCAD AI Connector MVP")) {
    # Remove Managed Host bundle from Autodesk ApplicationPlugins
    if (Test-Path -LiteralPath $hostPluginDir) {
        Remove-Item -LiteralPath $hostPluginDir -Recurse -Force
        Write-Host "[OK] Removed Managed Host bundle from ApplicationPlugins."
    }

    # Remove Desktop Agent install directory
    if (Test-Path -LiteralPath $appInstallDir) {
        Remove-Item -LiteralPath $appInstallDir -Recurse -Force
        Write-Host "[OK] Removed Desktop Agent files."
    }

    # Remove receipt
    if (Test-Path -LiteralPath $receiptPath) {
        Remove-Item -LiteralPath $receiptPath -Force
        Write-Host "[OK] Removed installation receipt."
    }

    Write-Host "AutoCAD AI Connector MVP uninstallation complete!"
}
