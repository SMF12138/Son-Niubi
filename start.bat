@echo off
REM Start script: launch Flask + desktop pet in background, open browser, no windows.
cd /d "%~dp0"

REM Create desktop shortcut on first run (points to start.vbs for windowless launch)
set "SHORTCUT=%USERPROFILE%\Desktop\Son niubi.lnk"
if not exist "%SHORTCUT%" (
  powershell -NoProfile -Command "$r='%~dp0'.TrimEnd('\'); $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:USERPROFILE+'\Desktop\Son niubi.lnk'); $s.TargetPath=Join-Path $r 'start.vbs'; $s.WorkingDirectory=$r; $s.IconLocation=(Join-Path $r 'data\tubiao.ico')+',0'; $s.WindowStyle=7; $s.Save()" >nul 2>&1
)

if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] .venv not found, run scripts\setup.ps1 first
  pause
  exit /b 1
)

REM If service already running, just open browser
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  start "" "http://127.0.0.1:8000"
  exit /b 0
)

REM Launch Flask service in background (windowless)
start "" ".venv\Scripts\pythonw.exe" -m app.cli serve --no-browser

REM Launch desktop pet (hidden console via vbs)
start "" wscript.exe launch_widget.vbs

REM Open browser immediately (page loads data on its own)
start "" "http://127.0.0.1:8000"

REM Exit this window cleanly, no lingering windows
exit /b 0