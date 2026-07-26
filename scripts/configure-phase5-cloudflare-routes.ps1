[CmdletBinding()]
param(
    [string]$Hostname = "cad.kythuatvang.com",
    [string]$GatewayService = "http://127.0.0.1:8765",
    [string]$PortalService = "http://127.0.0.1:3000",
    [string]$ConfigPath = (Join-Path $env:USERPROFILE ".cloudflared\config.yml")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Không tìm thấy Cloudflare config: $ConfigPath"
}
$cloudflared = Get-Command cloudflared -CommandType Application -ErrorAction Stop |
    Select-Object -First 1
$current = Get-Content -LiteralPath $ConfigPath -Raw
if ($current.Contains("# BEGIN AUTOCAD MCP PHASE5 ROUTES")) {
    throw "Cloudflare config đã có route Phase 5; không ghi đè lần hai."
}

$escapedHost = [regex]::Escape($Hostname)
$ownedRoutePattern = "(?m)^  - hostname: $escapedHost\r?\n    service: [^\r\n]+\r?\n"
$matches = [regex]::Matches($current, $ownedRoutePattern)
if ($matches.Count -ne 1) {
    throw "Không tìm thấy đúng một route hiện có cho $Hostname; từ chối sửa config."
}

$replacement = @"
  # BEGIN AUTOCAD MCP PHASE5 ROUTES
  - hostname: $Hostname
    path: ^/(mcp(?:/.*)?|agent/ws|api/(?:agent|portal)/.*|healthz|readyz|\.well-known/.*)$
    service: $GatewayService
  - hostname: $Hostname
    service: $PortalService
  # END AUTOCAD MCP PHASE5 ROUTES
"@
$updated = [regex]::Replace(
    $current,
    $ownedRoutePattern,
    $replacement + [Environment]::NewLine,
    1
)

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path (Split-Path -Parent $ConfigPath) "config.before-phase5-$stamp.yml"
Copy-Item -LiteralPath $ConfigPath -Destination $backup
[IO.File]::WriteAllText($ConfigPath, $updated, [Text.UTF8Encoding]::new($false))

& $cloudflared.Source tunnel --config $ConfigPath ingress validate
if ($LASTEXITCODE -ne 0) {
    Copy-Item -LiteralPath $backup -Destination $ConfigPath -Force
    throw "Cloudflare từ chối route Phase 5; config ban đầu đã được khôi phục."
}
Write-Host "Đã vá đúng route $Hostname; các route/setting khác được giữ nguyên." -ForegroundColor Green
Write-Host "Gateway: $GatewayService"
Write-Host "Portal:  $PortalService"
Write-Host "Backup dùng để rollback: $backup"
