@echo off
if not exist "%LOCALAPPDATA%\JobOps\Rollback JobFlow.cmd" (
  echo JobFlow has no installed rollback version yet.
  pause
  exit /b 2
)
call "%LOCALAPPDATA%\JobOps\Rollback JobFlow.cmd"
exit /b %ERRORLEVEL%
