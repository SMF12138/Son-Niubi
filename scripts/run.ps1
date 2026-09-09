$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "未找到 .venv,请先运行 .\scripts\setup.ps1"
    exit 1
}
$py = '.venv\Scripts\python.exe'

& $py -m app.cli serve
