$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "ERROR: .venv not found. Run .\scripts\setup.ps1 first."
    Write-Host "setup.ps1 needs Python 3.10+ on your PATH: https://www.python.org/downloads/"
    exit 1
}
$py = '.venv\Scripts\python.exe'

& $py -m app.cli serve
