#Requires -Version 5.1
# Login Cloudflare (mo browser), tao named tunnel, config, DNS, roi chay tunnel.
# Mac dinh: https://cad.kythuatvang.com -> http://127.0.0.1:8765

[CmdletBinding()]
param(
    [string]$Hostname = "cad.kythuatvang.com",
    [string]$TunnelName = "autocad-mcp",
    [string]$LocalService = "http://127.0.0.1:8765",
    [string]$CloudflaredPath = "",
    [switch]$SkipLogin,
    [switch]$SkipDns,
    [switch]$SkipRun,
    [switch]$KeepQuickTunnels,
    # Ghi de DNS cu (A/CNAME) bang CNAME tro ve tunnel. Mac dinh bat.
    [switch]$NoOverwriteDns
)

$ErrorActionPreference = "Stop"
$CloudflaredDir = Join-Path $env:USERPROFILE ".cloudflared"
$CertPath = Join-Path $CloudflaredDir "cert.pem"
$ConfigPath = Join-Path $CloudflaredDir "config.yml"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    OK: $Message" -ForegroundColor Green
}

function Write-WarnLine {
    param([string]$Message)
    Write-Host "    !! $Message" -ForegroundColor Yellow
}

function Resolve-Cloudflared {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath)) {
            throw "Khong tim thay cloudflared tai: $ExplicitPath"
        }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $cmd = Get-Command cloudflared -CommandType Application -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $pf86 = ${env:ProgramFiles(x86)}
    $candidates = @(
        (Join-Path $pf86 "cloudflared\cloudflared.exe"),
        (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe")
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }

    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetRoot) {
        $found = Get-ChildItem -LiteralPath $wingetRoot -Filter "cloudflared.exe" -Recurse -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }

    throw "Khong tim thay cloudflared.exe. Cai: winget install --id Cloudflare.cloudflared"
}

function Stop-QuickTunnels {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" -ErrorAction SilentlyContinue
    if (-not $procs) {
        Write-Ok "Khong co cloudflared dang chay."
        return
    }

    foreach ($proc in @($procs)) {
        $cmd = [string]$proc.CommandLine
        $isQuick = $cmd -match '(?i)tunnel\s+--url\b'
        $isNamedRun = $cmd -match '(?i)\brun\b'
        if ($isQuick -and -not $isNamedRun) {
            Write-WarnLine "Tat Quick Tunnel PID $($proc.ProcessId)"
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
        else {
            Write-Host "    Keep cloudflared PID $($proc.ProcessId)" -ForegroundColor DarkGray
        }
    }
}

function Test-OriginCert {
    return (Test-Path -LiteralPath $CertPath)
}

function Invoke-CloudflaredLogin {
    param([string]$Cf)

    Write-Step "Dang nhap Cloudflare (mo browser / hien link)"
    Write-Host ""
    Write-Host "  1) Trinh duyet se mo. Neu khong, copy URL ma cloudflared in ra." -ForegroundColor White
    Write-Host "  2) Login dung account so huu domain (vi du kythuatvang.com)." -ForegroundColor White
    Write-Host "  3) Chon zone domain do roi bam Authorize." -ForegroundColor White
    Write-Host "  4) Quay lai cua so nay, doi den khi co cert.pem." -ForegroundColor White
    Write-Host ""
    Write-Host "  Hostname dich : $Hostname" -ForegroundColor Green
    Write-Host "  Cert se luu tai: $CertPath" -ForegroundColor DarkGray
    Write-Host ""

    if (-not (Test-Path -LiteralPath $CloudflaredDir)) {
        New-Item -ItemType Directory -Path $CloudflaredDir -Force | Out-Null
    }

    & $Cf tunnel login
    $loginExit = $LASTEXITCODE

    if (-not (Test-OriginCert)) {
        $msg = "Login chua thanh cong: khong thay $CertPath. " +
            "Chay lai, Authorize dung zone. Exit cloudflared=$loginExit"
        throw $msg
    }

    Write-Ok "Da co origin cert: $CertPath"
}

function Get-LocalCredentialIds {
    if (-not (Test-Path -LiteralPath $CloudflaredDir)) {
        return @()
    }
    return @(
        Get-ChildItem -LiteralPath $CloudflaredDir -Filter "*.json" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' } |
            ForEach-Object { $_.BaseName.ToLowerInvariant() }
    )
}

function Get-TunnelCandidates {
    param(
        [string]$Cf,
        [string]$Name
    )

    $candidates = @()

    try {
        $jsonText = & $Cf tunnel list --output json 2>$null | Out-String
        if ($jsonText -and $jsonText.Trim().StartsWith("[")) {
            $items = $jsonText | ConvertFrom-Json
            foreach ($item in @($items)) {
                $n = [string]$item.name
                if ($n -and ($n -ieq $Name)) {
                    $candidates += [pscustomobject]@{
                        Id   = [string]$item.id
                        Name = $n
                    }
                }
            }
            return $candidates
        }
    }
    catch {
        # fallback text below
    }

    $text = & $Cf tunnel list 2>&1 | Out-String
    $escaped = [regex]::Escape($Name)
    $rx = [regex]::Matches(
        $text,
        '(?im)^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+(\S+)'
    )
    foreach ($m in $rx) {
        $id = $m.Groups[1].Value
        $n = $m.Groups[2].Value
        if ($n -ieq $Name) {
            $candidates += [pscustomobject]@{ Id = $id; Name = $n }
        }
    }
    return $candidates
}

function Resolve-TunnelIdWithCredentials {
    param(
        [string]$Cf,
        [string]$Name
    )

    $localCreds = Get-LocalCredentialIds
    $candidates = @(Get-TunnelCandidates -Cf $Cf -Name $Name)

    if ($candidates.Count -eq 0) {
        return $null
    }

    # Uu tien tunnel co file credentials local.
    foreach ($c in $candidates) {
        $idLower = $c.Id.ToLowerInvariant()
        if ($localCreds -contains $idLower) {
            Write-Ok "Chon tunnel co credentials local: $($c.Name) ($($c.Id))"
            return $c.Id
        }
    }

    # Neu chi 1 candidate, dung no (co the can recreate credentials).
    if ($candidates.Count -eq 1) {
        Write-WarnLine "Tunnel $($candidates[0].Name) ($($candidates[0].Id)) khong co file .json local."
        return $candidates[0].Id
    }

    # Nhieu ten trung (khac hoa/thuong): chon cai moi nhat theo list order cuoi.
    $pick = $candidates[-1]
    Write-WarnLine "Co $($candidates.Count) tunnel ten giong '$Name'. Chon: $($pick.Name) ($($pick.Id))"
    return $pick.Id
}

function Get-CredentialsPath {
    param([string]$TunnelId)

    $exact = Join-Path $CloudflaredDir "$TunnelId.json"
    if (Test-Path -LiteralPath $exact) {
        return $exact
    }

    # Windows path khong phan biet hoa thuong; thu lower.
    $lower = Join-Path $CloudflaredDir ($TunnelId.ToLowerInvariant() + ".json")
    if (Test-Path -LiteralPath $lower) {
        return $lower
    }

    return $null
}

function Ensure-NamedTunnel {
    param(
        [string]$Cf,
        [string]$Name
    )

    # Returns: @{ Id = ...; Name = ... }
    Write-Step "Named tunnel: $Name"
    $existing = Resolve-TunnelIdWithCredentials -Cf $Cf -Name $Name
    if ($existing) {
        $credPath = Get-CredentialsPath -TunnelId $existing
        if ($credPath) {
            Write-Ok "Tunnel san sang: $Name ($existing)"
            return @{ Id = $existing; Name = $Name }
        }

        Write-WarnLine "Tunnel $existing thieu credentials local. Thu tao tunnel moi ten khac."
        $altName = "$Name-local"
        $alt = Resolve-TunnelIdWithCredentials -Cf $Cf -Name $altName
        if ($alt -and (Get-CredentialsPath -TunnelId $alt)) {
            Write-Ok "Dung tunnel $altName ($alt)"
            return @{ Id = $alt; Name = $altName }
        }

        Write-Host "    Tao tunnel $altName ..." -ForegroundColor DarkGray
        $createOut = & $Cf tunnel create $altName 2>&1 | Out-String
        Write-Host $createOut
        $idMatch = [regex]::Match(
            $createOut,
            '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
        )
        if ($idMatch.Success) {
            $newId = $idMatch.Groups[1].Value
            Write-Ok "Da tao $altName ($newId)"
            return @{ Id = $newId; Name = $altName }
        }
    }

    Write-Host "    Tao tunnel moi: $Name ..." -ForegroundColor DarkGray
    $createOut = & $Cf tunnel create $Name 2>&1 | Out-String
    Write-Host $createOut

    $idMatch = [regex]::Match(
        $createOut,
        '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    )
    if ($idMatch.Success) {
        $id = $idMatch.Groups[1].Value
        Write-Ok "Da tao tunnel $Name ($id)"
        return @{ Id = $id; Name = $Name }
    }

    $again = Resolve-TunnelIdWithCredentials -Cf $Cf -Name $Name
    if ($again) {
        Write-Ok "Tunnel: $Name ($again)"
        return @{ Id = $again; Name = $Name }
    }

    throw "Khong lay duoc Tunnel ID sau khi create. Output: $createOut"
}

function Write-TunnelConfig {
    param(
        [string]$TunnelId,
        [string]$HostnameValue,
        [string]$Service
    )

    Write-Step "Ghi config: $ConfigPath"

    $cred = Get-CredentialsPath -TunnelId $TunnelId
    if (-not $cred) {
        throw "Thieu credentials file cho tunnel $TunnelId trong $CloudflaredDir. Hay xoa tunnel do tren Dashboard roi chay lai de tao moi."
    }
    Write-Ok "Credentials: $cred"

    $credYaml = $cred -replace '\\', '/'
    $lines = @(
        '# Auto-generated by setup-cloudflare-tunnel.ps1'
        "# Public: https://$HostnameValue  ->  $Service"
        "tunnel: $TunnelId"
        "credentials-file: $credYaml"
        ''
        'ingress:'
        "  - hostname: $HostnameValue"
        "    service: $Service"
        '  - service: http_status:404'
        ''
    )
    $content = $lines -join "`n"

    if (-not (Test-Path -LiteralPath $CloudflaredDir)) {
        New-Item -ItemType Directory -Path $CloudflaredDir -Force | Out-Null
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ConfigPath, $content, $utf8NoBom)
    Write-Ok "Da ghi config.yml"
    Write-Host $content -ForegroundColor DarkGray
}

function Ensure-DnsRoute {
    param(
        [string]$Cf,
        [string]$Name,
        [string]$HostnameValue,
        [bool]$OverwriteDns = $true
    )

    Write-Step "DNS route: $HostnameValue -> tunnel $Name"

    function Test-DnsRouteSuccess {
        param([string]$Text, [int]$Code)
        if ($Text -match '(?i)Added CNAME|which will route to this tunnel|successfully|already configured') {
            return $true
        }
        if ($Code -eq 0) {
            return $true
        }
        return $false
    }

    function Invoke-RouteDns {
        param([switch]$Force)

        $argsList = New-Object System.Collections.Generic.List[string]
        [void]$argsList.Add("tunnel")
        [void]$argsList.Add("route")
        [void]$argsList.Add("dns")
        if ($Force) {
            [void]$argsList.Add("--overwrite-dns")
        }
        [void]$argsList.Add($Name)
        [void]$argsList.Add($HostnameValue)

        # cloudflared ghi INF ra stderr; khong de native error lam throw.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = & $Cf $argsList.ToArray() 2>&1 | ForEach-Object { "$_" } | Out-String
            $code = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $prevEap
        }

        return @{
            ExitCode = $code
            Output   = $output
        }
    }

    $result = Invoke-RouteDns
    Write-Host $result.Output

    if (-not (Test-DnsRouteSuccess -Text $result.Output -Code $result.ExitCode)) {
        $exists = $result.Output -match '(?i)already exists|record with that host already|Failed to create record|code:\s*1003'
        if ($exists -and $OverwriteDns) {
            Write-WarnLine "DNS cu dang ton tai. Ghi de bang CNAME tunnel (--overwrite-dns)..."
            $result = Invoke-RouteDns -Force
            Write-Host $result.Output
        }
    }

    if (Test-DnsRouteSuccess -Text $result.Output -Code $result.ExitCode) {
        Write-Ok "DNS route da xu ly."
    }
    else {
        Write-WarnLine "route dns exit=$($result.ExitCode). Kiem tra DNS tren Dashboard neu public van 530."
        Write-WarnLine "Can CNAME $HostnameValue -> <TUNNEL_ID>.cfargotunnel.com (Proxied)."
    }
}

function Test-LocalOrigin {
    param([string]$Service)

    $uri = [Uri]$Service
    $port = $uri.Port
    if ($port -le 0) {
        if ($uri.Scheme -eq "https") {
            $port = 443
        }
        else {
            $port = 80
        }
    }

    $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        Write-Ok "Local origin dang listen port $port"
        return $true
    }

    Write-WarnLine "Chua thay process listen $port ($Service)."
    Write-WarnLine "Hay chay start_mcp_chatgpt.bat o cua so khac de MCP lang nghe 8765."
    return $false
}

function Start-NamedTunnel {
    param(
        [string]$Cf,
        [string]$Name
    )

    Write-Step "Chay named tunnel (giu cua so nay mo)"
    Write-Host "    Public MCP: https://$Hostname/mcp" -ForegroundColor Green
    Write-Host "    Local:      $LocalService" -ForegroundColor Green
    Write-Host "    Config:     $ConfigPath" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Nhan Ctrl+C de dung tunnel." -ForegroundColor Yellow
    Write-Host ""

    & $Cf tunnel --config $ConfigPath run $Name
    exit $LASTEXITCODE
}

# --- Main ---

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " AutoCAD MCP - Cloudflare Named Tunnel Setup" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Hostname : $Hostname"
Write-Host " Tunnel   : $TunnelName"
Write-Host " Origin   : $LocalService"
Write-Host " Cloud dir: $CloudflaredDir"

$cf = Resolve-Cloudflared -ExplicitPath $CloudflaredPath
Write-Step "cloudflared: $cf"
$ver = & $cf --version 2>&1 | Out-String
Write-Host "    $($ver.Trim())" -ForegroundColor DarkGray

if (-not $KeepQuickTunnels) {
    Write-Step "Don Quick Tunnel rac"
    Stop-QuickTunnels
}

$needLogin = -not (Test-OriginCert)
if ($SkipLogin) {
    if ($needLogin) {
        throw "SkipLogin nhung chua co $CertPath. Bo -SkipLogin de dang nhap."
    }
    Write-Ok "Bo qua login (cert da co)."
}
elseif ($needLogin) {
    Invoke-CloudflaredLogin -Cf $cf
}
else {
    Write-Step "Origin cert da ton tai - bo qua login"
    Write-Ok $CertPath
    Write-Host "    Login lai: xoa cert.pem roi chay lai script." -ForegroundColor DarkGray
}

Write-Step "Kiem tra quyen tunnel list"
$listProbe = & $cf tunnel list 2>&1 | Out-String
if ($listProbe -match '(?i)Cannot determine default origin certificate|Error locating origin cert|failed to load') {
    throw "cert.pem khong hop le. Chay lai (khong -SkipLogin). Chi tiet: $listProbe"
}
Write-Ok "tunnel list OK"

$tunnelInfo = Ensure-NamedTunnel -Cf $cf -Name $TunnelName
$tunnelId = $tunnelInfo.Id
$resolvedTunnelName = $tunnelInfo.Name
Write-TunnelConfig -TunnelId $tunnelId -HostnameValue $Hostname -Service $LocalService

if (-not $SkipDns) {
    Ensure-DnsRoute -Cf $cf -Name $resolvedTunnelName -HostnameValue $Hostname -OverwriteDns (-not $NoOverwriteDns)
}
else {
    Write-WarnLine "SkipDns: ban tu cau hinh CNAME tren Dashboard."
}

Write-Step "Kiem tra MCP local"
[void](Test-LocalOrigin -Service $LocalService)

Write-Host ""
Write-Host "---------------------------------------------" -ForegroundColor Green
Write-Host " Setup xong. Thong tin ChatGPT:" -ForegroundColor Green
Write-Host "   MCP URL : https://$Hostname/mcp" -ForegroundColor Green
Write-Host "   Metadata: https://$Hostname/.well-known/oauth-protected-resource" -ForegroundColor Green
Write-Host "   Tunnel  : $resolvedTunnelName ($tunnelId)" -ForegroundColor Green
Write-Host "---------------------------------------------" -ForegroundColor Green
Write-Host ""
Write-Host " Thu tu hang ngay:" -ForegroundColor White
Write-Host "   1) start_mcp_chatgpt.bat          (MCP :8765)"
Write-Host "   2) start_cloudflare_tunnel.bat    (tunnel nay)"
Write-Host "   3) ChatGPT OAuth -> https://$Hostname/mcp"
Write-Host ""

if ($SkipRun) {
    Write-WarnLine "SkipRun: khong chay tunnel. Chay tay:"
    Write-Host "    & `"$cf`" tunnel --config `"$ConfigPath`" run $resolvedTunnelName" -ForegroundColor Yellow
    exit 0
}

Start-Sleep -Seconds 2
Start-NamedTunnel -Cf $cf -Name $resolvedTunnelName
