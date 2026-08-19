@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\update-installed-jobflow.ps1"
set "JOBFLOW_UPDATE_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_UPDATE_EXIT%"=="0" (
  echo JobFlow update did not finish. The current version was kept; review the message above.
) else (
  echo JobFlow update check finished safely.
)
pause
exit /b %JOBFLOW_UPDATE_EXIT%
