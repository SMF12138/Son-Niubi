@echo off
REM One-click installer: runs scripts\setup.ps1 with the execution policy
REM bypassed for this single call (does not change any system setting).
setlocal
cd /d "%~dp0.."
echo Installing Son Niubi - creating .venv and installing dependencies...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo Setup FAILED with exit code %RC%. Read the messages above.
) else (
  echo Setup finished. Now double-click start.bat in this folder to run.
)
pause
endlocal
