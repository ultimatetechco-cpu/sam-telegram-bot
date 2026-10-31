@echo off
title Telegram AutoPost Pro
color 0B
cd /d "%~dp0"
echo ========================================================
echo   Starting Telegram AutoPost Pro Server...
echo ========================================================
echo.
echo Opening Dashboard at http://localhost:8080 ...
start "" "http://localhost:8080"
echo.
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Python stopped with error code %ERRORLEVEL%.
)
pause
