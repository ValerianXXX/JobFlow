@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-jobflow.ps1"
set "JOBFLOW_INSTALL_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_INSTALL_EXIT%"=="0" (
  echo.
  echo JobFlow installation did not finish. Keep this window open and review the message above.
) else (
  echo JobFlow installation is ready. The local app is opening now.
  start "JobFlow" "%LOCALAPPDATA%\JobOps\Start JobFlow.cmd"
)
pause
exit /b %JOBFLOW_INSTALL_EXIT%
