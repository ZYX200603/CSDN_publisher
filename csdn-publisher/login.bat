@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Please run setup.bat first.
  pause
  exit /b 1
)
echo ============================================================
echo   Log in to CSDN
echo ============================================================
echo A Chrome window will open. Log in there ^(QR code or password^).
echo This window waits up to 5 minutes and saves the login state
echo automatically, then closes the browser by itself.
echo ============================================================
echo.
call "%~dp0csdn.bat" login
echo.
echo Done. You can now use the tool ^(see README.md^).
pause
