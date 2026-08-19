@echo off
if not exist "%LOCALAPPDATA%\JobOps\Update JobFlow.cmd" (
  echo JobFlow is not installed in the fixed application directory. Run Install JobFlow.cmd first.
  pause
  exit /b 2
)
call "%LOCALAPPDATA%\JobOps\Update JobFlow.cmd"
exit /b %ERRORLEVEL%
