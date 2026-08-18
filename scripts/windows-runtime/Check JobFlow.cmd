@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\check-installed-jobflow.ps1"
set "JOBFLOW_CHECK_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %JOBFLOW_CHECK_EXIT%
