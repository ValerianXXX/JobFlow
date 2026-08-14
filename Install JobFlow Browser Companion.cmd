@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-jobflow-browser-companion.ps1"
set "JOBFLOW_COMPANION_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_COMPANION_EXIT%"=="0" (
  echo Browser Companion setup did not finish. Keep this window open and review the message above.
) else (
  echo Browser Companion setup helper finished. Complete the numbered browser steps shown above.
)
pause
exit /b %JOBFLOW_COMPANION_EXIT%
