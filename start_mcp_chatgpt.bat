@echo off
setlocal

rem Quick-start wrapper for the Phase 4 OAuth production MCP server.

cd /d "%~dp0"

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo Khong tim thay Windows PowerShell.
    pause
    exit /b 1
)

if not exist "%~dp0scripts\run-phase4-oauth.ps1" (
    echo Khong tim thay scripts\run-phase4-oauth.ps1.
    pause
    exit /b 1
)

echo Dang khoi dong AutoCAD MCP Phase 4 cho ChatGPT...
echo URL MCP: https://cad.kythuatvang.com/mcp
echo Giu cua so nay mo trong luc su dung. Nhan Ctrl+C de dung.
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0scripts\run-phase4-oauth.ps1" ^
    -PublicBaseUrl "https://cad.kythuatvang.com" ^
    -OAuthIssuer "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/" ^
    -OAuthAudience "https://cad.kythuatvang.com/" ^
    -Backend auto

if errorlevel 1 (
    echo.
    echo MCP dung voi loi. Kiem tra log loi o phia tren.
    pause
)

endlocal
