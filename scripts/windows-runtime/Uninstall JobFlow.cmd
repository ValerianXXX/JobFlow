@echo off
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\uninstall-installed-jobflow.ps1" -InstallRoot "%~dp0"
set "JOBFLOW_UNINSTALL_EXIT=%ERRORLEVEL%"
echo.
if not "%JOBFLOW_UNINSTALL_EXIT%"=="0" (
  echo JobFlow uninstall did not finish. Keep this window open and review the message above.
) else (
  echo JobFlow application files were removed. Local profile data was preserved.
)
pause
exit /b %JOBFLOW_UNINSTALL_EXIT%
