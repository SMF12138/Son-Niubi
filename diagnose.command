#!/bin/bash
# Son NiuBi 一键诊断 / One-click diagnostics
# 双击运行, 把窗口内容截图发给开发者 / Double-click, then screenshot this window.
cd "$(dirname "$0")"
HERE="$(pwd -P)"

echo "================================================"
echo " Son NiuBi 诊断报告 / Diagnostics"
echo "================================================"
echo ""
echo "[时间/Time] $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "[安装目录/Folder] $HERE"
echo ""

echo "--- 版本指纹 / Version fingerprints ---"
if [ -f ".venv/bin/python" ]; then echo ".venv: OK"; else echo ".venv: MISSING 缺失"; fi
if grep -q "Son NiuBi.app" scripts/setup.sh 2>/dev/null; then
    echo "launcher: v2.0.16+ (.app icon)"
else
    echo "launcher: OLD 旧版 (.command, no icon)"
fi
if grep -q "_PTS" app/models/moex_dir.py 2>/dev/null; then
    echo "N=7 confidence: smooth OK"
else
    echo "N=7 confidence: OLD 旧版"
fi
if grep -q "cell_rate" app/models/longhorizon.py 2>/dev/null; then
    echo "N=60 confidence: smooth OK"
else
    echo "N=60 confidence: OLD 旧版"
fi
if grep -q 'live_date\[5:\]' floating_pet.py 2>/dev/null; then
    echo "pet date format: MM-DD HH:MM OK"
else
    echo "pet date format: OLD 旧版"
fi
echo ""

echo "--- 桌面快捷方式 / Desktop launchers ---"
ls -ld "$HOME/Desktop/"*[Nn]iu* 2>/dev/null || echo "(无 / none)"
echo ""

echo "--- 8000 端口服务 / Service on port 8000 ---"
curl -s --noproxy '*' --max-time 3 http://127.0.0.1:8000/api/health || echo "(无服务 / no service running)"
echo ""
echo ""

echo "--- 本文件夹数据 / Local data files ---"
ls -la data/moex_live.json data/forecast_7.json 2>/dev/null || echo "(数据文件缺失 / data files missing)"
echo ""
echo "moex_live.json 内容:"
cat data/moex_live.json 2>/dev/null || echo "(不存在 / missing)"
echo ""
echo "forecast_7.json 关键字段:"
if [ -f data/forecast_7.json ]; then
    /usr/bin/python3 - <<'PYEOF' 2>/dev/null || head -c 400 data/forecast_7.json
import json
d = json.load(open('data/forecast_7.json'))
dr = d.get('direction', {})
print('as_of     =', d.get('as_of'))
print('predict   =', dr.get('prediction'))
print('confidence=', dr.get('confidence'))
PYEOF
else
    echo "(不存在 / missing)"
fi
echo ""

echo "--- MOEX 实时盘价抓取测试 / MOEX live fetch test ---"
if [ -f ".venv/bin/python" ]; then
  .venv/bin/python - <<'PYEOF'
import datetime as dt
import os
import traceback

proxy = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}
print("proxy env:", proxy or "(none 无代理)")

from app.data import http

# 用上一个工作日做测试日(周末/假日当天无 K 线属正常, 不代表网络问题)
d = dt.date.today()
for _ in range(7):
    if d.weekday() < 5:
        break
    d -= dt.timedelta(days=1)
url = ("https://iss.moex.com/iss/engines/currency/markets/selt/boards/CETS/"
       "securities/CNYRUB_TOM/candles.json"
       f"?from={d}&till={d}&interval=1&iss.meta=off")
print("test day:", d, "(weekday" , d.weekday(), ")")

print("[1] 直连测试(含代理失败自动改直连):")
try:
    raw = http.open_url(url, timeout=8).read()
    print("    HTTP OK, bytes =", len(raw))
except Exception:
    print("    HTTP FAILED 失败, 完整报错如下:")
    traceback.print_exc()

print("[2] 业务函数 fetch_latest():")
try:
    from app.data.moex_live import fetch_latest
    print("   ", fetch_latest())
except Exception:
    traceback.print_exc()
PYEOF
else
  echo "(.venv 缺失, 跳过 / .venv missing, skipped)"
fi
echo ""

echo "--- 运行中的进程 / Running processes ---"
ps aux | grep -E "app\.cli serve|floating_pet" | grep -v grep || echo "(无 / none)"
echo ""

echo "================================================"
echo " 诊断完成。请把本窗口【全部内容】截图发送。"
echo " Done. Please screenshot this ENTIRE window."
echo "================================================"
echo ""
read -r -p "按回车键关闭 / Press Enter to close..." _
