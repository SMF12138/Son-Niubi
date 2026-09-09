$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/3] 创建虚拟环境 .venv"
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}

$py = '.venv\Scripts\python.exe'
Write-Host "[2/3] 安装依赖 (requirements.txt)"
& $py -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "默认源失败,改用清华镜像重试…"
    & $py -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }
}

$vendor = Join-Path $root 'app\web\static\vendor\echarts.min.js'
Write-Host "[3/3] 准备 ECharts 本地资源"
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
        Write-Warning 'ECharts 下载失败。请手动下载 echarts.min.js 放入 app\web\static\vendor\ 后重试。'
    } else {
        Write-Host "ECharts 已就位: $((Get-Item $vendor).Length) bytes"
    }
} else {
    Write-Host 'ECharts 已存在,跳过'
}

& $py -c "import flask,pandas,numpy,statsmodels,sklearn;print('依赖验证: ok')"
Write-Host "设置完成。运行 .\scripts\run.ps1 启动仪表盘。"
