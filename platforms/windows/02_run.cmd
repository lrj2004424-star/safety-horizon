@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Run platforms/windows/01_install.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" horizon.py configure
set "result=%errorlevel%"
pause
exit /b %result%
