@echo off
cd /d "%~dp0"
echo Stopping rate forecast service + desktop pet...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -match 'app.cli serve|floating_pet' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force; Write-Host ('stopped pid ' + $_.ProcessId) } catch {} }"
echo All stopped.
timeout /t 3 >nul
exit /b 0
