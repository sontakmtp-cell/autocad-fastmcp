@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   LIEN KET THIET BI VOI TAI KHOAN GOOGLE / AUTH0
echo ========================================================
echo.

uv run --project apps/desktop_agent python scripts/pair-device.py

echo.
pause
endlocal
