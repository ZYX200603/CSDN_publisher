@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   CSDN Publisher - first time setup
echo ============================================================
echo.

set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY goto NOPY
echo [1/4] Python found:
%PY% --version
echo.

if exist ".venv\Scripts\python.exe" (
  echo [2/4] .venv already exists, skipping.
) else (
  echo [2/4] Creating .venv ...
  %PY% -m venv .venv
  if not exist ".venv\Scripts\python.exe" goto NOVENV
)
echo.

echo [3/4] Installing dependencies ^(playwright + Pillow^) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo [!] Default index failed, retrying with the Tsinghua mirror ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
echo.

echo [4/4] Checking for Google Chrome ...
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  echo [OK] Chrome found.
) else (
  if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    echo [OK] Chrome found.
  ) else (
    echo [!] Chrome was not found in the default location.
    echo     Either install Google Chrome, or download Playwright's own browser:
    echo         .venv\Scripts\python.exe -m playwright install chromium
    echo     The tool still runs without Chrome if you do the above.
  )
)
echo.
echo ============================================================
echo   Setup finished. Next step: double-click login.bat
echo ============================================================
pause
exit /b 0

:NOPY
echo [X] Python not found.
echo     Install Python 3.10+ from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" in the installer.
pause
exit /b 1

:NOVENV
echo [X] Failed to create .venv.
echo     Try running this file as administrator, or create it manually:
echo         python -m venv .venv
pause
exit /b 1
