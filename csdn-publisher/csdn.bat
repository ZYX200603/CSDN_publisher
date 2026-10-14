@echo off
chcp 65001 >nul
REM CSDN publisher launcher - uses the project venv automatically.
REM Usage: csdn.bat login ^| check ^| inspect ^| config ^| publish posts\demo.md
"%~dp0.venv\Scripts\python.exe" "%~dp0publish_csdn.py" %*
