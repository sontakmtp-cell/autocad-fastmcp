[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$ConfigPath = (Join-Path $env:USERPROFILE ".cloudflared\config.yml")
)

$ErrorActionPreference = "Stop"
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$current = Get-Content -LiteralPath $ConfigPath -Raw
if (-not $current.Contains("# BEGIN AUTOCAD MCP PHASE5 ROUTES")) {
    throw "Config hiện tại không có marker Phase 5; từ chối ghi đè."
}
$cloudflared = Get-Command cloudflared -CommandType Application -ErrorAction Stop |
    Select-Object -First 1
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$safetyCopy = Join-Path (Split-Path -Parent $ConfigPath) "config.before-phase5-rollback-$stamp.yml"
Copy-Item -LiteralPath $ConfigPath -Destination $safetyCopy
Copy-Item -LiteralPath $resolvedBackup -Destination $ConfigPath -Force

& $cloudflared.Source tunnel --config $ConfigPath ingress validate
if ($LASTEXITCODE -ne 0) {
    Copy-Item -LiteralPath $safetyCopy -Destination $ConfigPath -Force
    throw "Backup không hợp lệ; config Phase 5 đã được khôi phục."
}
Write-Host "Đã rollback Cloudflare bằng backup được chỉ định." -ForegroundColor Green
Write-Host "Safety copy: $safetyCopy"
