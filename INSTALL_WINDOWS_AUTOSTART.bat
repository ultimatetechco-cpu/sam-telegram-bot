@echo off
title Install Telegram AutoPost Pro Autostart
color 0a
cls
echo =====================================================================
echo    INSTALL TELEGRAM AUTOPOST PRO - PERMANENT AUTO-START ON BOOT
echo =====================================================================
echo.
echo This script will add Telegram AutoPost Pro to Windows Startup.
echo Every time you turn on your PC, the service will run SILENTLY
echo in the background, keeping your website 100%% online 24/7!
echo.

set SCRIPT_DIR=%~dp0
set VBS_TARGET=%SCRIPT_DIR%START_BACKGROUND_SERVICE.vbs
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_DIR%\TelegramAutoPostPro.lnk

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%VBS_TARGET%\"'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo =====================================================================
    echo  [SUCCESS] Autostart shortcut installed successfully!
    echo  Shortcut Location: %SHORTCUT_PATH%
    echo.
    echo  From now on, whenever you start Windows, your Telegram AutoPost
    echo  service will start automatically in the background!
    echo =====================================================================
) else (
    echo.
    echo [ERROR] Failed to create shortcut in Startup folder.
)

echo.
pause
