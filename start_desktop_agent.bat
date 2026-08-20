@echo off
setlocal
cd /d "%~dp0"

echo Dang kiem tra va dong Desktop Agent cu neu dang chay...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$procs = Get-Process -Name 'KythuatvangAutoCADAgent' -ErrorAction SilentlyContinue; if ($procs) { Write-Host ('Dang dong Desktop Agent cu (PID: ' + ($procs.Id -join ', ') + ')...') -ForegroundColor Yellow; $procs | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800 }"

echo Dang khoi dong Desktop Agent voi day du QUYEN DOC VA GHI (Managed Write)...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$identityFile = Join-Path $env:LOCALAPPDATA 'Kythuatvang\AutoCADAgent\identity\device.json'; $deviceId = ''; if (Test-Path -LiteralPath $identityFile) { $idJson = Get-Content -LiteralPath $identityFile -Raw | ConvertFrom-Json; $deviceId = $idJson.device_id }; if (-not $deviceId) { $deviceId = 'autocad-lab-01' }; & '%~dp0scripts\run-phase6-agent.ps1' -AllowedDeviceId $deviceId -EnableManagedWrite"

endlocal
