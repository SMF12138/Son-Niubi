@echo off
REM Start: Flask + desktop pet. Browser opens when switching pet form.
cd /d "%~dp0"

REM Desktop shortcut (ask on first run only; no silent system changes)
set "SHORTCUT=%USERPROFILE%\Desktop\Son niubi.lnk"
if not exist "%SHORTCUT%" (
  choice /C YN /M "Create a desktop shortcut for Son niubi"
  if errorlevel 2 goto skip_shortcut
  powershell -NoProfile -Command "$r='%~dp0'.TrimEnd('\'); $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut($env:USERPROFILE+'\Desktop\Son niubi.lnk'); $s.TargetPath=Join-Path $r 'start.bat'; $s.WorkingDirectory=$r; $s.IconLocation=(Join-Path $r 'data\tubiao.ico')+',0'; $s.WindowStyle=7; $s.Save()" >nul 2>&1
)
:skip_shortcut

if not exist ".venv\Scripts\pythonw.exe" (
  echo.
  echo ERROR: .venv not found.
  echo Run scripts\setup.bat first to create it and install dependencies.
  echo Requires Python 3.10+ from https://www.python.org/downloads/
  echo During Python setup, tick 'Add python.exe to PATH'.
  echo.
  pause
  exit /b 1
)

REM Identify what holds port 8000:
REM   0 = service from THIS folder (pet only), 2 = another/older install (kill+restart), 1 = nothing
REM Paths are normalized (absolutized, trailing slash, drive-letter case) before compare.
REM Pre-v2.0.7 servers have no app_root field -> treated as foreign and restarted.
powershell -NoProfile -Command "$here=[IO.Path]::GetFullPath((Resolve-Path -LiteralPath '%~dp0').Path).TrimEnd('\'); try { $j=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 } catch { exit 1 }; if ([string]::IsNullOrWhiteSpace($j.app_root)) { exit 2 }; try { $r=[IO.Path]::GetFullPath($j.app_root).TrimEnd('\') } catch { exit 2 }; if ($r -ieq $here) { exit 0 } else { exit 2 }" >nul 2>&1
if %errorlevel%==0 (
  start "" wscript.exe launch_widget.vbs
  exit /b 0
)
if not %errorlevel%==2 goto launch_flask
echo Port 8000 is held by another or older install folder. Stopping it so this folder can run...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*floating_pet.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
set TRIES=0
:waitport
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 goto portfree
set /a TRIES+=1
if %TRIES% GEQ 6 goto portfree
timeout /t 1 /nobreak >nul
goto waitport
:portfree

:launch_flask
REM Launch Flask (background, no window, no browser)
start "" ".venv\Scripts\pythonw.exe" -m app.cli serve --no-browser

REM Launch desktop pet (hidden console via vbs)
start "" wscript.exe launch_widget.vbs

REM Exit this cmd window
exit /b 0