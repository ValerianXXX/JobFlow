@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check-jobflow.ps1"
set "JOBFLOW_CHECK_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_CHECK_EXIT%"=="0" (
  echo JobFlow needs attention. Keep this window open and follow the first failed check above.
) else (
  echo JobFlow is ready. You may close this window.
)
pause
exit /b %JOBFLOW_CHECK_EXIT%
