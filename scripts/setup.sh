#!/bin/bash
# One-click installer for macOS (and Linux)
# 1. Finds an existing Python 3.10+
# 2. If missing or too old, AUTO-INSTALLS one (coexists with system Python, never touches it):
#    - macOS with Homebrew: brew install python python-tk   (no password needed)
#    - macOS without brew : downloads python.org official universal2 pkg and
#      installs it (asks for ONE admin password - macOS security requirement)
#    - Linux: apt / dnf / pacman
# 3. Creates .venv, installs dependencies, downloads ECharts if missing
# 注意: 不用 set -e —— 更新场景下 pip 单包下载失败不应阻止桌面快捷方式创建
# (旧 .venv 仍可运行); 只有"首次安装且依赖无法导入"才是致命错误, 显式检查。
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

# Linux(Debian/Ubuntu)坑: 系统 python3 存在但缺 venv/ensurepip 模块时, 建出的
# .venv 里没有 pip, 依赖安装必然失败。创建 venv 前先探测, 缺就补装。
if [ "$(uname -s)" = "Linux" ] && ! "$PY_EXE" -c "import ensurepip" 2>/dev/null; then
    echo "[auto] python3 缺 venv/ensurepip 模块, 补装 python3-venv ..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y python3-venv python3-full
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm --needed python
    fi
    if ! "$PY_EXE" -c "import ensurepip" 2>/dev/null; then
        echo "ERROR: python3-venv 安装失败, 请手动执行: sudo apt install python3-venv 后重跑本脚本"
        exit 1
    fi
    echo "[auto] python3-venv 就绪"
fi

echo "[2/5] Creating virtual environment .venv ..."
if [ ! -f ".venv/bin/python" ]; then
    "$PY_EXE" -m venv .venv || { echo "ERROR: 创建 .venv 失败"; exit 1; }
fi

PY=".venv/bin/python"
echo "[3/5] Installing dependencies (requirements.txt) ..."
# 更新场景下 pip 失败不致命: 旧 .venv 里已有依赖, 下方 import 检查兜底
"$PY" -m pip install --disable-pip-version-check -q -r requirements.txt \
    || echo "WARNING: pip 安装有失败项(若是更新版本, 旧依赖通常仍可运行, 继续)"

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

if ! "$PY" -c "import flask, pandas, numpy" 2>/dev/null; then
    echo "ERROR: 核心依赖缺失且 pip 安装失败, 请检查网络后重新运行本脚本"
    exit 1
fi
echo "Dependencies OK"

echo "[5/5] Checking tkinter (桌宠依赖) ..."
if "$PY" -c "import tkinter" 2>/dev/null; then
    echo "tkinter OK (桌宠可用)"
else
    echo "WARNING: 未找到 tkinter —— 仪表盘可正常使用, 但桌宠无法启动。"
    echo "         Homebrew 用户请执行: brew install python-tk"
    echo "         Debian/Ubuntu 用户请执行: sudo apt install python3-tk"
fi

# tar 包在 Windows 上打包会丢执行位; 这里统一补上, 保证 ./start.sh 和
# 双击 setup.command / start.command / stop.command 直接可用
chmod +x setup.command start.command stop.command diagnose.command start.sh stop.sh scripts/*.sh 2>/dev/null || true

# 桌面启动器: 创建 .app 包(图标内置, 100% 可靠显示), 双击即可启动。
# 关桌宠会自动杀后台(v2.0.9 起), 不再需要"停止"快捷方式; stop.sh 仍保留
# 供桌宠崩溃时手动兜底。
APP="$HOME/Desktop/Son NiuBi.app"
if [ -d "$HOME/Desktop" ]; then
    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

    # Info.plist: 指定可执行文件和图标
    cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Son NiuBi</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundleIdentifier</key>
    <string>com.sonniubi.launcher</string>
    <key>CFBundleName</key>
    <string>Son NiuBi</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.1.3</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
</dict>
</plist>
PLIST

    # 可执行脚本: 必须经 Terminal.app 启动 start.sh, 不能直接 exec。
    # 原因: 双击 .app 时 LaunchServices 不提供终端, 而 start.sh 把 Flask/桌宠
    # 都放后台后立即退出 -> .app 主进程随之结束 -> 后台 fork 的 tkinter 桌宠
    # 被孤立/回收, 窗口起不来, 全程无终端看不到报错, 表现就是"点击没反应"。
    # 经 Terminal 启动: ①有可见终端和日志, 出错不静默; ②后台进程挂在 Terminal
    # 会话下, 不随 .app 主进程退出被回收。
    # 外层 heredoc 不带引号以展开 $PWD; 内层 'APPLESCRIPT' 带引号原样写入文件;
    # \\" 在外层生成字面 \"(AppleScript 字符串里转义内层 bash 路径的双引号),
    # 路径含空格(Son NiuBi)由这对内层双引号保证不断词。
    cat > "$APP/Contents/MacOS/Son NiuBi" <<LAUNCHER
#!/bin/bash
/usr/bin/osascript <<'APPLESCRIPT'
tell application "Terminal"
    activate
    do script "bash \\"$PWD/start.sh\\""
end tell
APPLESCRIPT
LAUNCHER
    chmod +x "$APP/Contents/MacOS/Son NiuBi"

    # 图标: 用 data/tubiao.png 生成 icon.icns 放进 Resources, 系统自动显示
    ICON_SRC="$PWD/data/tubiao.png"
    if [ -f "$ICON_SRC" ]; then
        ICONSET="$(mktemp -d)/icon.iconset"
        mkdir -p "$ICONSET"
        # 用 .venv 的 Python+PIL 生成各尺寸 PNG(不依赖 sips, 兼容性更好)
        "$PY" - "$ICON_SRC" "$ICONSET" <<'PYEOF' 2>/dev/null
import sys
try:
    from PIL import Image
    img = Image.open(sys.argv[1])
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    sizes = {
        "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
    }
    for name, sz in sizes.items():
        r = img.resize((sz, sz), Image.LANCZOS)
        r.save(f"{sys.argv[2]}/{name}")
except Exception as e:
    print(f"PIL icon error: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
        PIL_OK=$?
        if [ "$PIL_OK" -eq 0 ] && iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns" >/dev/null 2>&1; then
            echo "Desktop launcher created: 'Son NiuBi.app' (with icon)"
        elif [ "$PIL_OK" -eq 0 ] && command -v sips >/dev/null 2>&1; then
            # PIL 成功但 iconutil 失败: 回退用 sips 逐个生成再 iconutil
            sips -z 128 128 "$ICON_SRC" --out "$ICONSET/icon_128x128.png" >/dev/null 2>&1
            sips -z 256 256 "$ICON_SRC" --out "$ICONSET/icon_256x256.png" >/dev/null 2>&1
            sips -z 512 512 "$ICON_SRC" --out "$ICONSET/icon_512x512.png" >/dev/null 2>&1
            if iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns" >/dev/null 2>&1; then
                echo "Desktop launcher created: 'Son NiuBi.app' (with icon via sips fallback)"
            else
                echo "Desktop launcher created: 'Son NiuBi.app' (iconutil failed, using default)"
            fi
        else
            echo "Desktop launcher created: 'Son NiuBi.app' (icon generation failed, using default)"
        fi
        rm -rf "$(dirname "$ICONSET")"
    else
        echo "Desktop launcher created: 'Son NiuBi.app' (no icon source)"
    fi

    # 清理旧版 .command 快捷方式(如果还在)
    rm -f "$HOME/Desktop/Son NiuBi.command" "$HOME/Desktop/Son NiuBi 停止.command"
else
    echo "(桌面启动器创建失败, 不影响使用)"
fi

echo ""
echo "Setup complete. Start the dashboard with ./start.sh"
