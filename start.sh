#!/bin/bash
# Start: Flask + desktop pet (macOS/Linux)
cd "$(dirname "$0")"
# 物理路径(解析符号链接), 与服务端 config.ROOT(Path.resolve) 口径保持一致
HERE="$(pwd -P)"

if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: .venv not found."
    echo "Run scripts/setup.sh first to create it and install dependencies."
    echo "Requires Python 3.10+ from https://www.python.org/downloads/"
    exit 1
fi

PY=".venv/bin/python"
mkdir -p logs

# 归一化为物理绝对路径; 目录不可达时回退为原值
norm_path() {
    local p
    p="$(cd -- "$1" 2>/dev/null && pwd -P)" || p="$1"
    printf '%s' "$p"
}

# 判断 8000 端口上是否已有服务, 以及它来自哪个安装目录。
# 关键: 不能只看"端口有没有人", 多版本文件夹并存时, 旧文件夹的服务可能占着
# 端口 —— 那种服务只会更新旧文件夹的数据, 本文件夹桌宠会永远读到冻结的预测。
# 注意: v2.0.7 之前的服务 health 不返回 app_root, 这种情况也必须按外来/旧服务处理。
HEALTH_BODY="$(curl -s --noproxy '*' --max-time 2 "http://127.0.0.1:8000/api/health" 2>/dev/null)"

if [ -n "$HEALTH_BODY" ]; then
    RUNNING_ROOT="$(printf '%s' "$HEALTH_BODY" | "$PY" -c '
import json, sys
try:
    print(json.load(sys.stdin).get("app_root", ""))
except Exception:
    print("")
' 2>/dev/null)"

    SAME=0
    if [ -n "$RUNNING_ROOT" ]; then
        RR="$(norm_path "$RUNNING_ROOT")"
        [ "$RR" = "$HERE" ] && SAME=1
    fi

    if [ "$SAME" = "1" ]; then
        echo "Flask from this folder already running, starting pet only..."
        "$PY" floating_pet.py > logs/pet.log 2>&1 &
        exit 0
    fi

    # 端口被【别的文件夹】或【旧版本(无 app_root)】的服务占着:
    # 停掉它(含旧桌宠), 改用本文件夹启动。
    if [ -n "$RUNNING_ROOT" ]; then
        echo "Port 8000 is held by another install:"
        echo "  $RUNNING_ROOT"
    else
        echo "Port 8000 is held by an older-version service (no app_root in health)."
    fi
    echo "Stopping it so this folder's service can run..."
    pkill -f "app.cli serve" 2>/dev/null
    pkill -f "floating_pet.py" 2>/dev/null
    # 等端口释放(最多 ~6 秒), 否则本服务会因单实例锁退出
    for i in 1 2 3 4 5 6; do
        curl -s --noproxy '*' --max-time 1 "http://127.0.0.1:8000/api/health" >/dev/null 2>&1 || break
        sleep 1
    done
fi

# Launch Flask in background (no browser)
echo "Starting Flask server..."
nohup "$PY" -m app.cli serve --no-browser > /dev/null 2>&1 &
FLASK_PID=$!
echo "Flask PID: $FLASK_PID"

# Wait a moment for Flask to start
sleep 2

# Launch desktop pet
echo "Starting desktop pet..."
"$PY" floating_pet.py > logs/pet.log 2>&1 &
PET_PID=$!
echo "Pet PID: $PET_PID"

echo ""
echo "Son NiuBi is running."
echo "  Dashboard: http://127.0.0.1:8000"
echo "  Stop:      ./stop.sh"
