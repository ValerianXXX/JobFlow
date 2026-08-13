@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check-release-readiness.ps1"
set "JOBFLOW_RELEASE_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_RELEASE_EXIT%"=="0" (
  echo JobFlow is not ready for public release yet. No upload was attempted.
) else (
  echo JobFlow passed every local and confirmed human release gate. No upload was attempted.
)
pause
exit /b %JOBFLOW_RELEASE_EXIT%
