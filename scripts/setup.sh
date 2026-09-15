#!/bin/bash
# One-click installer for macOS (and Linux)
# 1. Finds an existing Python 3.10+
# 2. If missing or too old, AUTO-INSTALLS one (coexists with system Python, never touches it):
#    - macOS with Homebrew: brew install python python-tk   (no password needed)
#    - macOS without brew : downloads python.org official universal2 pkg and
#      installs it (asks for ONE admin password - macOS security requirement)
#    - Linux: apt / dnf / pacman
# 3. Creates .venv, installs dependencies, downloads ECharts if missing
set -e
cd "$(dirname "$0")/.."

echo "[1/5] Locating Python 3.10+ ..."

# --- helper: echo the first candidate whose version is >= 3.10 ---
find_py() {
    local cmd ver major minor
    for cmd in "$@"; do
        command -v "$cmd" &>/dev/null || continue
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
        major=${ver%%.*}
        minor=${ver#*.}
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

PY_EXE=$(find_py python3 python || true)

# 常见安装位置兜底(python.org 官方包 / Homebrew), PATH 里没有也能找到
if [ -z "$PY_EXE" ]; then
    PY_EXE=$(find_py \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 || true)
fi

# --- 自动安装: 没有或版本不达标时, 并排装一个新版 (不动系统自带 Python) ---
auto_install_python() {
    echo ""
    echo "[auto] 未找到 Python 3.10+, 开始自动安装 (与系统自带版本并存, 不修改系统) ..."
    if command -v brew &>/dev/null; then
        echo "[auto] 检测到 Homebrew: brew install python (全程无需密码)"
        if brew install python; then
            brew install python-tk || \
                echo "[auto] python-tk 安装失败: 看板不受影响, 桌宠可稍后执行 brew install python-tk"
            return 0
        fi
        echo "[auto] brew 安装 python 失败, 改用 python.org 官方安装包 ..."
    fi
    case "$(uname -s)" in
        Darwin)
            local ver pkg ok=false i
            ver="3.12.8"
            pkg="python-${ver}-macos11.pkg"   # universal2: Intel / Apple Silicon 通用
            echo "[auto] 下载 python.org 官方安装包 ${pkg} (~70MB) ..."
            rm -f "/tmp/${pkg}"
            for base in \
                "https://registry.npmmirror.com/-/binary/python/${ver}" \
                "https://mirrors.huawei.com/python/${ver}" \
                "https://www.python.org/ftp/python/${ver}"; do
                echo "        try ${base}/${pkg}"
                # 官方 pkg 实际 ~46MB(实测 46118924 字节); 阈值只用来拦截
                # HTML 报错页/截断文件, 不能大于真实包
                if curl -fL --max-time 900 "${base}/${pkg}" -o "/tmp/${pkg}" \
                   && [ "$(wc -c < "/tmp/${pkg}" | tr -d ' ')" -gt 30000000 ]; then
                    ok=true
                    break
                fi
            done
            if [ "$ok" != true ]; then
                echo ""
                echo "ERROR: 安装包下载失败。请手动安装 Python:"
                echo "  https://www.python.org/downloads/  (安装时勾选 tkinter, 桌宠需要)"
                echo ""
                return 1
            fi
            echo "[auto] 开始安装, 需要输入一次管理员密码 (macOS 安全机制, 仅此一次)"
            if ! sudo installer -pkg "/tmp/${pkg}" -target /; then
                echo ""
                echo "ERROR: 安装失败。请双击 /tmp/${pkg} 手动安装后重新运行本脚本"
                echo ""
                return 1
            fi
            rm -f "/tmp/${pkg}"
            return 0
            ;;
        Linux)
            if command -v apt-get &>/dev/null; then
                sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-tk
            elif command -v dnf &>/dev/null; then
                sudo dnf install -y python3 python3-tkinter
            elif command -v yum &>/dev/null; then
                sudo yum install -y python3 python3-tkinter
            elif command -v pacman &>/dev/null; then
                sudo pacman -S --noconfirm python
            else
                echo "ERROR: 未识别的发行版, 请手动安装 Python 3.10+ 后重新运行"
                return 1
            fi
            ;;
        *)
            echo "ERROR: 不支持的系统 $(uname -s), 请手动安装 Python 3.10+ 后重新运行"
            return 1
            ;;
    esac
}

if [ -z "$PY_EXE" ]; then
    auto_install_python || exit 1
    # 安装后重新探测 (新装解释器可能还没进当前终端的 PATH)
    PY_EXE=$(find_py python3 python \
        /usr/local/bin/python3.12 /usr/local/bin/python3.13 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /opt/homebrew/bin/python3 || true)
    if [ -z "$PY_EXE" ]; then
        echo "ERROR: 自动安装已完成但仍未探测到 Python, 请关闭终端重新运行本脚本"
        exit 1
    fi
fi
echo "      OK ($PY_EXE)"

echo "[2/5] Creating virtual environment .venv ..."
if [ ! -f ".venv/bin/python" ]; then
    "$PY_EXE" -m venv .venv
fi

PY=".venv/bin/python"
echo "[3/5] Installing dependencies (requirements.txt) ..."
"$PY" -m pip install --disable-pip-version-check -q -r requirements.txt

VENDOR="app/web/static/vendor/echarts.min.js"
echo "[4/5] Preparing local ECharts asset ..."
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

echo "[5/5] Checking tkinter (桌宠依赖) ..."
if "$PY" -c "import tkinter" 2>/dev/null; then
    echo "tkinter OK (桌宠可用)"
else
    echo "WARNING: 未找到 tkinter —— 仪表盘可正常使用, 但桌宠无法启动。"
    echo "         Homebrew 用户请执行: brew install python-tk"
fi

# tar 包在 Windows 上打包会丢执行位; 这里统一补上, 保证 ./start.sh 和
# 双击 setup.command / start.command / stop.command 直接可用
chmod +x setup.command start.command stop.command start.sh stop.sh scripts/*.sh 2>/dev/null || true

echo ""
echo "Setup complete. Start the dashboard with ./start.sh"
