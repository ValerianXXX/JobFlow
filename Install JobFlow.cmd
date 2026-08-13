@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-jobflow.ps1"
if errorlevel 1 (
  echo.
  echo JobFlow installation did not finish. Keep this window open and review the message above.
  pause
)
