@echo off
title Push Telegram AutoPost Pro to GitHub
color 0a
cls
echo =====================================================================
echo       PUSH TELEGRAM AUTOPOST PRO TO GITHUB
echo =====================================================================
echo.
echo Target Repo: https://github.com/ultimatetechco-cpu/sam-telegram-bot.git
echo.
echo Pushing code to main branch...
echo (If a browser window pops up, click "Sign in with your browser" / "Authorize")
echo.

cd /d "%~dp0"
git push -u origin main

echo.
echo =====================================================================
echo  If push succeeded, return to Render.com to deploy your service!
echo =====================================================================
pause
