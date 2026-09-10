$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "ERROR: .venv not found. Run scripts\setup.bat (or .\scripts\setup.ps1) first."
    Write-Host "Requires Python 3.10+ from https://www.python.org/downloads/"
    Write-Host "During Python setup, tick 'Add python.exe to PATH', then reopen your terminal."
    exit 1
}
$py = '.venv\Scripts\python.exe'

& $py -m app.cli serve
