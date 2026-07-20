@echo off
setlocal
cd /d "%~dp0"

title AutoCAD MCP - Cloudflare Named Tunnel

echo.
echo =============================================
echo  Cloudflare Named Tunnel cho ChatGPT MCP
echo  Public: https://cad.kythuatvang.com/mcp
echo  Local : http://127.0.0.1:8765
echo =============================================
echo.
echo  Buoc 1 (neu can): Browser mo - login Cloudflare
echo            chon domain kythuatvang.com - Authorize
echo  Buoc 2: Script tu tao tunnel + config + DNS
echo  Buoc 3: Chay tunnel (giu cua so nay mo)
echo.
echo  Luu y: Hay chay start_mcp_chatgpt.bat o cua so khac
echo         de MCP lang nghe cong 8765.
echo.
echo ---------------------------------------------
echo.

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo Khong tim thay Windows PowerShell.
    pause
    exit /b 1
)

if not exist "%~dp0scripts\setup-cloudflare-tunnel.ps1" (
    echo Khong tim thay scripts\setup-cloudflare-tunnel.ps1
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0scripts\setup-cloudflare-tunnel.ps1" ^
    -Hostname "cad.kythuatvang.com" ^
    -TunnelName "autocad-mcp" ^
    -LocalService "http://127.0.0.1:8765"

set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
    echo Tunnel/script dung voi ma loi %EXITCODE%.
    echo Xem thong bao o tren. Neu thieu cloudflared:
    echo   winget install --id Cloudflare.cloudflared
    pause
    exit /b %EXITCODE%
)

echo Tunnel da dung.
pause
endlocal
