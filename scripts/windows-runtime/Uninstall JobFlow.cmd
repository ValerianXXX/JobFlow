@echo off
setlocal
cd /d "%~dp0"
set "JOBFLOW_UNINSTALL_SCRIPT=%TEMP%\jobflow-uninstall-%RANDOM%-%RANDOM%.ps1"
copy /y "%~dp0bin\uninstall-installed-jobflow.ps1" "%JOBFLOW_UNINSTALL_SCRIPT%" >nul
if errorlevel 1 (
  echo Unable to prepare the JobFlow uninstaller.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%JOBFLOW_UNINSTALL_SCRIPT%" -InstallRoot "%~dp0"
set "JOBFLOW_UNINSTALL_EXIT=%ERRORLEVEL%"
del /q "%JOBFLOW_UNINSTALL_SCRIPT%" >nul 2>nul
echo.
if not "%JOBFLOW_UNINSTALL_EXIT%"=="0" (
  echo JobFlow uninstall did not finish. Keep this window open and review the message above.
) else (
  echo JobFlow application files were removed. Local profile data was preserved.
)
pause
exit /b %JOBFLOW_UNINSTALL_EXIT%
