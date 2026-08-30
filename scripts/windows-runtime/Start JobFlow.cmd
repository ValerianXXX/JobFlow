@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bin\start-installed-jobflow.ps1"
if errorlevel 1 (
  echo.
  echo JobFlow stopped before it was ready. Keep this window open and review the message above.
  pause
)
