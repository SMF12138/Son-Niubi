$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/4] Locating Python 3.10+ ..."
$check = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
$pyExe = $null
$pyPre = @()

# 1) 'python' on PATH -- skip the Microsoft Store execution-alias stub,
#    which only pops up the Store instead of running Python
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd -and $cmd.Source -and $cmd.Source -notlike '*WindowsApps*') {
    & python -c $check 2>$null
    if ($LASTEXITCODE -eq 0) { $pyExe = 'python' }
}

# 2) 'py -3' launcher -- provided by the python.org installer even when
#    'Add python.exe to PATH' was left unticked
if (-not $pyExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
    & py -3 -c $check 2>$null
    if ($LASTEXITCODE -eq 0) { $pyExe = 'py'; $pyPre = @('-3') }
}

# 3) scan the default per-user install location, highest version first
if (-not $pyExe) {
    $bases = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*" -Directory -ErrorAction SilentlyContinue |
             Sort-Object Name -Descending
    foreach ($b in $bases) {
        $cand = Join-Path $b.FullName 'python.exe'
        if (Test-Path $cand) {
            & $cand -c $check 2>$null
            if ($LASTEXITCODE -eq 0) { $pyExe = $cand; break }
        }
    }
}

if (-not $pyExe) {
    Write-Host ""
    Write-Host "ERROR: no usable Python 3.10+ found. All of these were tried:"
    Write-Host "  1) 'python' on PATH"
    Write-Host "  2) 'py -3' launcher"
    Write-Host "  3) $env:LOCALAPPDATA\Programs\Python\Python3*"
    Write-Host ""
    Write-Host "How to fix:"
    Write-Host "  - Run the Python installer again and TICK 'Add python.exe to PATH',"
    Write-Host "    then CLOSE and REOPEN your terminal before re-running this script."
    Write-Host "  - If you installed Python from the Microsoft Store, remove it and"
    Write-Host "    install from https://www.python.org/downloads/ instead."
    Write-Host ""
    exit 1
}
Write-Host "      OK ($pyExe $pyPre)"

Write-Host "[2/4] Creating virtual environment .venv ..."
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $pyExe @pyPre -m venv .venv
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        Write-Host "ERROR: 'venv' creation failed. See the messages above."
        exit 1
    }
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
