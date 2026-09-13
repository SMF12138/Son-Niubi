#!/bin/bash
# One-click installer for macOS (and Linux)
# Creates .venv, installs dependencies, downloads ECharts
set -e
cd "$(dirname "$0")/.."

echo "[1/4] Locating Python 3.10+ ..."

# Try python3 first, then python
PY_EXE=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PY_EXE="$cmd"
            break
        fi
    fi
done

if [ -z "$PY_EXE" ]; then
    echo ""
    echo "ERROR: Python 3.10+ not found."
    echo ""
    echo "Install Python:"
    echo "  macOS:  brew install python3    (or download from https://www.python.org/downloads/)"
    echo "  Linux:  sudo apt install python3 (or equivalent for your distro)"
    echo ""
    exit 1
fi
echo "      OK ($PY_EXE)"

echo "[2/4] Creating virtual environment .venv ..."
if [ ! -f ".venv/bin/python" ]; then
    "$PY_EXE" -m venv .venv
fi

PY=".venv/bin/python"
echo "[3/4] Installing dependencies (requirements.txt) ..."
"$PY" -m pip install --disable-pip-version-check -q -r requirements.txt

VENDOR="app/web/static/vendor/echarts.min.js"
echo "[4/4] Preparing local ECharts asset ..."
if [ ! -f "$VENDOR" ]; then
    mkdir -p "$(dirname "$VENDOR")"
    OK=false
    for url in \
        "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js" \
        "https://unpkg.com/echarts@5.5.1/dist/echarts.min.js" \
        "https://registry.npmmirror.com/echarts/5.5.1/files/dist/echarts.min.js"; do
        if curl -sL --max-time 60 "$url" -o "$VENDOR" && [ $(wc -c < "$VENDOR") -gt 500000 ]; then
            OK=true
            break
        fi
    done
    if [ "$OK" = false ]; then
        echo "WARNING: ECharts download failed. Download manually into app/web/static/vendor/"
    else
        echo "ECharts ready: $(wc -c < "$VENDOR") bytes"
    fi
else
    echo "ECharts already present, skipping"
fi

"$PY" -c "import flask, pandas, numpy; print('Dependencies OK')"

# tkinter 是桌宠依赖;python.org 官方包自带, Homebrew Python 默认缺失
if "$PY" -c "import tkinter" 2>/dev/null; then
    echo "tkinter OK (桌宠可用)"
else
    echo "WARNING: 未找到 tkinter —— 仪表盘可正常使用, 但桌宠无法启动。"
    echo "         Homebrew 用户请执行: brew install python-tk"
fi

echo ""
echo "Setup complete. Start the dashboard with ./start.sh"
