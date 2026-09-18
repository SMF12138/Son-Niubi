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
function Get-PyFromDefaultDirs {
    $bases = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*" -Directory -ErrorAction SilentlyContinue |
             Sort-Object Name -Descending
    foreach ($b in $bases) {
        $cand = Join-Path $b.FullName 'python.exe'
        if (Test-Path $cand) {
            & $cand -c $check 2>$null
            if ($LASTEXITCODE -eq 0) { return $cand }
        }
    }
    return $null
}
if (-not $pyExe) { $pyExe = Get-PyFromDefaultDirs }

# --- 自动安装: 没有 Python 或版本不达标时, 静默安装一个新版并存(per-user, 无需管理员) ---
function Install-PythonAuto {
    $ver = '3.12.8'
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $exe = "$env:TEMP\python-$ver-$arch.exe"
    $uris = @(
        "https://registry.npmmirror.com/-/binary/python/$ver/python-$ver-$arch.exe",
        "https://mirrors.huawei.com/python/$ver/python-$ver-$arch.exe",
        "https://www.python.org/ftp/python/$ver/python-$ver-$arch.exe"
    )
    Write-Host "[auto] no usable Python 3.10+ found - auto-installing $ver ($arch, per-user, coexists with any old version) ..."
    $ok = $false
    foreach ($u in $uris) {
        try {
            Write-Host "        try $u"
            $ProgressPreference = 'SilentlyContinue'
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            (New-Object System.Net.WebClient).DownloadFile($u, $exe)
            # 官方 exe 实际 ~27MB(amd64 实测 27043760 字节); 阈值只用来
            # 拦截 HTML 报错页/截断文件, 不能大于真实包
            if ((Get-Item $exe).Length -gt 15MB) { $ok = $true; break }
        } catch { Write-Host "        download failed: $($_.Exception.Message)" }
    }
    if (-not $ok) {
        Write-Host "ERROR: installer download failed. Install Python manually from"
        Write-Host "       https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),"
        Write-Host "       then CLOSE and REOPEN your terminal and re-run this script."
        return $false
    }
    Write-Host "        installing silently (per-user, no admin password needed) ..."
    $p = Start-Process -FilePath $exe -ArgumentList @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=1',
            'Include_tkinter=1', 'Include_test=0', 'Include_launcher=1', '/norestart'
         ) -Wait -PassThru
    Remove-Item $exe -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) {
        Write-Host "ERROR: silent install failed (exit $($p.ExitCode)). Run '$exe' manually,"
        Write-Host "       or install from https://www.python.org/downloads/ and re-run this script."
        return $false
    }
    return $true
}

if (-not $pyExe) {
    if (-not (Install-PythonAuto)) { exit 1 }
    # 安装完成: PrependPath 只影响新终端, 当前会话直接扫默认目录
    $pyExe = Get-PyFromDefaultDirs
    if (-not $pyExe) {
        # winget 安装路径兜底
        $wg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Python*" -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
              Select-Object -First 1
        if ($wg) { $pyExe = $wg.FullName }
    }
    if (-not $pyExe) {
        Write-Host "ERROR: auto-install finished but Python not detected. CLOSE and REOPEN your"
        Write-Host "       terminal, then re-run this script."
        exit 1
    }
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

# 桌面快捷方式(失败不阻塞安装): 启动=桌宠+看板。
# 关桌宠会自动杀后台(v2.0.9 起), 不再需要"停止"快捷方式; stop.bat 仍保留
# 供桌宠崩溃时手动兜底。
try {
    $ws = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnk = $ws.CreateShortcut((Join-Path $desktop 'Son NiuBi 启动.lnk'))
    $lnk.TargetPath = Join-Path $root 'start.vbs'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = Join-Path $root 'data\tubiao.ico'
    $lnk.Save()
    Write-Host "Desktop shortcut created: 'Son NiuBi 启动'"
} catch {
    Write-Host "(桌面快捷方式创建失败, 不影响使用: $($_.Exception.Message))"
}
Write-Host "Setup complete. Start the dashboard with .\scripts\run.ps1"
