@echo off
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\rollback-installed-jobflow.ps1"
set "JOBFLOW_ROLLBACK_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_ROLLBACK_EXIT%"=="0" (
  echo JobFlow rollback did not finish. Keep this window open and review the message above.
) else (
  echo JobFlow rollback request completed. Review the result above, then restart JobFlow if a version changed.
)
pause
exit /b %JOBFLOW_ROLLBACK_EXIT%
