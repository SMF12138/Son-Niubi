$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/4] Checking Python 3.10+ ..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "ERROR: 'python' was not found on your PATH."
    Write-Host "Install Python 3.10 or newer: https://www.python.org/downloads/"
    Write-Host "During install, tick 'Add python.exe to PATH'."
    Write-Host ""
    exit 1
}
& python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Python 3.10 or newer is required."
    Write-Host "Check your version with: python --version"
    Write-Host "If that command opens the Microsoft Store, install Python instead from"
    Write-Host "https://www.python.org/downloads/"
    Write-Host ""
    exit 1
}
Write-Host "      OK"

Write-Host "[2/4] Creating virtual environment .venv ..."
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}

$py = '.venv\Scripts\python.exe'
Write-Host "[3/4] Installing dependencies (requirements.txt) ..."
& $py -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Default index failed, retrying with Tsinghua mirror ..."
    & $py -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

$vendor = Join-Path $root 'app\web\static\vendor\echarts.min.js'
Write-Host "[4/4] Preparing local ECharts asset ..."
if (-not (Test-Path $vendor)) {
    New-Item -ItemType Directory -Force (Split-Path $vendor) | Out-Null
    $ok = $false
    foreach ($uri in @(
        'https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js',
        'https://unpkg.com/echarts@5.5.1/dist/echarts.min.js',
        'https://registry.npmmirror.com/echarts/5.5.1/files/dist/echarts.min.js'
    )) {
        try {
            Invoke-WebRequest -Uri $uri -OutFile $vendor -TimeoutSec 60
            if ((Get-Item $vendor).Length -gt 500KB) { $ok = $true; break }
        } catch { }
    }
    if (-not $ok) {
        Write-Warning 'ECharts download failed. Download echarts.min.js manually into app\web\static\vendor\ and re-run.'
    } else {
        Write-Host "ECharts ready: $((Get-Item $vendor).Length) bytes"
    }
} else {
    Write-Host 'ECharts already present, skipping'
}

& $py -c "import flask, pandas, numpy; print('Dependencies OK')"
Write-Host "Setup complete. Start the dashboard with .\scripts\run.ps1"
