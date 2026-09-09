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
echo [WAIT] polling health check (fast startup, no fixed wait)...
set /a tries=0
:waitloop
set /a tries+=1
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 goto up
if %tries% geq 30 (
  echo [WARN] service not ready after 60s, continuing anyway
  goto up
)
ping -n 3 127.0.0.1 >nul
goto waitloop
:up
echo [DONE] service is up (after ~%tries%x2s)
echo [DONE] launching desktop pet widget...
start /min "" cmd /c "cd /d "%~dp0" && launch_widget.bat"
echo [DONE] opening web page
start "" "http://127.0.0.1:8000"
echo.
echo Service + widget running. Auto-updates daily at 09:00.
echo Closing this window will NOT stop them. To stop, run stop.bat
ping -n 6 127.0.0.1 >nul
exit /b 0
