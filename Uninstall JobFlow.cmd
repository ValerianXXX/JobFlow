@echo off
if not exist "%LOCALAPPDATA%\JobOps\Uninstall JobFlow.cmd" (
  echo JobFlow is not installed in the fixed application directory.
  pause
  exit /b 2
)
call "%LOCALAPPDATA%\JobOps\Uninstall JobFlow.cmd"
exit /b %ERRORLEVEL%
