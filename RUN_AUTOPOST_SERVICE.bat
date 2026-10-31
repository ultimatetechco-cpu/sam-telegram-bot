@echo off
title Telegram AutoPost Pro - 24/7 Live Sync Service
color 0b
cls
echo =====================================================================
echo       TELEGRAM AUTOPOST PRO - LIVE AUTOMATED WATCHDOG
echo =====================================================================
echo.
echo Starting AutoSync Watchdog Service...
echo This will keep your Python backend, Cloudflare tunnel, and Hostinger domain
echo in 100%% continuous sync.
echo.

cd /d "%~dp0"

set PYTHON_CMD=python
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
) else if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Python\bin\python.exe"
)

"%PYTHON_CMD%" auto_sync_service.py
pause
