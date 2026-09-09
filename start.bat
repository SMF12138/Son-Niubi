@echo off
cd /d "%~dp0"

set "SHORTCUT=%USERPROFILE%\Desktop\Son niubi.lnk"
set "APPROOT=%~dp0"
if not exist "%SHORTCUT%" (
  powershell -NoProfile -Command "$r=$env:APPROOT.TrimEnd('\'); $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:SHORTCUT); $s.TargetPath=Join-Path $r 'start.bat'; $s.WorkingDirectory=$r; $s.IconLocation=(Join-Path $r 'data\tubiao.ico')+',0'; $s.WindowStyle=7; $s.Save()" >nul 2>&1
)

echo ================================
echo   CNY/RUB Exchange Rate Forecast
echo   Rate Forecast System - starting
echo ================================
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] .venv not found, run scripts\setup.ps1 first
  pause
  exit /b 1
)
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  echo [INFO] service already running, opening web page
  start "" "http://127.0.0.1:8000"
  exit /b 0
)
echo [START] launching service in background...
start "" ".venv\Scripts\pythonw.exe" -m app.cli serve --no-browser
echo [WAIT] warming up, about 30 seconds...
ping -n 31 127.0.0.1 >nul
echo [DONE] opening web page
start "" "http://127.0.0.1:8000"
echo.
echo Service is running in background. Auto-updates daily at 09:00.
echo Closing this window will NOT stop the service. To stop, run stop.bat
ping -n 6 127.0.0.1 >nul
exit /b 0
