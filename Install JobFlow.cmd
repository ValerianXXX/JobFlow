@echo off
setlocal
cd /d "%~dp0"
set "JOBFLOW_INSTALL_ARGUMENT="
if "%~1"=="" goto run_installer
if /i "%~1"=="-NoLaunch" if "%~2"=="" (
  set "JOBFLOW_INSTALL_ARGUMENT=-NoLaunch"
  goto run_installer
)
echo JobFlow installer accepts only the optional -NoLaunch argument.
set "JOBFLOW_INSTALL_EXIT=64"
goto install_finished

:run_installer
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0scripts\install-jobflow-v2.ps1" %JOBFLOW_INSTALL_ARGUMENT%
set "JOBFLOW_INSTALL_EXIT=%ERRORLEVEL%"

:install_finished
echo.
if not "%JOBFLOW_INSTALL_EXIT%"=="0" (
  echo.
  if "%JOBFLOW_INSTALL_EXIT%"=="2" (
    echo JobFlow requires a signed schema-v2 complete-runtime release. Nothing was activated.
  ) else if "%JOBFLOW_INSTALL_EXIT%"=="6" (
    echo JobFlow safely recovered an interrupted install. Run Install JobFlow.cmd again.
  ) else (
    echo JobFlow installation did not finish. Keep this window open and review the message above.
  )
) else (
  if defined JOBFLOW_INSTALL_ARGUMENT (
    echo JobFlow installation is ready. Automatic launch was skipped.
  ) else (
    if not exist "%LOCALAPPDATA%\JobOps\bin\start-installed-jobflow.ps1" (
      echo JobFlow was installed, but its verified start control is unavailable.
      set "JOBFLOW_INSTALL_EXIT=3"
    ) else (
      echo JobFlow installation is ready. The local app is opening now.
      start "JobFlow" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\JobOps\bin\start-installed-jobflow.ps1"
    )
  )
)
pause
exit /b %JOBFLOW_INSTALL_EXIT%
