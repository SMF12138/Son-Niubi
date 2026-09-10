@echo off
REM Start: Flask + desktop pet. Browser opens when switching pet form.
cd /d "%~dp0"

REM Desktop shortcut (first run only)
set "SHORTCUT=%USERPROFILE%\Desktop\Son niubi.lnk"
if not exist "%SHORTCUT%" (
  powershell -NoProfile -Command "$r='%~dp0'.TrimEnd('\'); $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:USERPROFILE+'\Desktop\Son niubi.lnk'); $s.TargetPath=Join-Path $r 'start.bat'; $s.WorkingDirectory=$r; $s.IconLocation=(Join-Path $r 'data\tubiao.ico')+',0'; $s.WindowStyle=7; $s.Save()" >nul 2>&1
)

if not exist ".venv\Scripts\pythonw.exe" (
  echo .venv not found
  pause
  exit /b 1
)

REM If service already running, just start pet
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  start /b "" ".venv\Scripts\python.exe" floating_pet.py
  exit /b 0
)

REM Launch Flask (background, no window, no browser)
start "" ".venv\Scripts\pythonw.exe" -m app.cli serve --no-browser

REM Launch desktop pet (background, no console window)
start /b "" ".venv\Scripts\python.exe" floating_pet.py

REM Open browser (once, on startup only)
start "" "http://127.0.0.1:8000"

REM Exit this cmd window
exit /b 0