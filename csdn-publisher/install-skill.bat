@echo off
chcp 65001 >nul
echo Installing the csdn-publish skill for WorkBuddy ...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-skill.ps1"
if errorlevel 1 (
  echo.
  echo [!] Install script failed. Manual fallback:
  echo     copy the folder  skills\csdn-publish  into your agent's skills folder.
)
echo.
pause
