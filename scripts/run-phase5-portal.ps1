[CmdletBinding()]
param([switch]$Dev)

$ErrorActionPreference = "Stop"
$portalRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "apps\web_portal"
if (-not (Test-Path -LiteralPath (Join-Path $portalRoot ".env.local"))) {
    throw "Chưa có cấu hình Portal. Hãy chạy provision-phase5-portal.ps1 trước."
}
Push-Location $portalRoot
try {
    if ($Dev) {
        & npm run dev
    }
    else {
        & npm run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & npm run start
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
