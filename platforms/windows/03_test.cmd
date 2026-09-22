@echo off
setlocal
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" horizon.py test
set "result=%errorlevel%"
pause
exit /b %result%
