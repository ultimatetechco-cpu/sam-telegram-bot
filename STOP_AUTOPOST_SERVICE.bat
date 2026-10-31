@echo off
title Stop Telegram AutoPost Pro Service
color 0c
cls
echo =====================================================================
echo         STOPPING TELEGRAM AUTOPOST PRO BACKGROUND SERVICES
echo =====================================================================
echo.

taskkill /F /IM cloudflared.exe >nul 2>&1
echo [+] Stopped cloudflared.exe (Tunnel)

rem Kill python process running on port 8080 if any
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8080 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    echo [+] Stopped Python Backend (PID %%a)
)

echo.
echo =====================================================================
echo  Services stopped successfully.
echo =====================================================================
pause
