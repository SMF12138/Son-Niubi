@echo off
cd /d "%~dp0"
echo Stopping rate forecast service...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | Where-Object { $_.CommandLine -match 'app.cli serve' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force; Write-Host ('stopped pid ' + $_.ProcessId) } catch {} }"
echo Service stopped.
timeout /t 3 >nul
exit /b 0
