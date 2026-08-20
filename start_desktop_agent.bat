@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Dang kiem tra va dong sach cac tien trinh Desktop Agent cu dang chay...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$agentProcs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'KythuatvangAutoCADAgent' -or ($_.CommandLine -match 'autocad_desktop_agent|autocad-desktop-agent|run-phase5-agent|run-phase6-agent') }; foreach ($p in $agentProcs) { if ($p.ProcessId -ne $PID) { Write-Host ('[STOP] Dang dong tien trinh Agent (PID ' + $p.ProcessId + ': ' + $p.Name + ')...') -ForegroundColor Yellow; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } }; Start-Sleep -Milliseconds 600"

echo [2/2] Dang khoi dong Desktop Agent (phien ban moi nhat, ho tro Managed Write / Phase 8)...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$identityFile = Join-Path $env:LOCALAPPDATA 'Kythuatvang\AutoCADAgent\identity\device.json'; $deviceId = ''; if (Test-Path -LiteralPath $identityFile) { $idJson = Get-Content -LiteralPath $identityFile -Raw | ConvertFrom-Json; $deviceId = $idJson.device_id }; if (-not $deviceId) { $deviceId = 'autocad-lab-01' }; & '%~dp0scripts\run-phase6-agent.ps1' -AllowedDeviceId $deviceId -EnableManagedWrite -Source %*"

endlocal
