@echo off
chcp 65001 >nul
REM CSDN read-stats launcher - uses the project venv automatically.
REM Usage: csdn_stats.bat [--pages N] [--report-only] [--top N]
"%~dp0.venv\Scripts\python.exe" "%~dp0csdn_stats.py" %*
