"""MOEX 方向信号健康监控。

每日计算最近窗口(默认 90 天)已实现的 7 日方向命中率, 与历史基准对比,
衰减到阈值(默认 55%)以下时输出告警。守护 MOEX 套利偏离信号不被
未来的汇率机制/资本管制变化悄悄侵蚀。

用法: python -m app.monitor_signal
输出 JSON: {rolling_acc, n, baseline, status, alert}
"""
import json
import logging

import numpy as np

from app import config

log = logging.getLogger(__name__)

WINDOW_DAYS = 90       # 滚动评估窗口(已实现的 7 日预测)
N_HOR = 7
# 阈值。2026-09-10 对齐生产窗口(config.ZDEV_WINDOW=150)后实测:
#   win=150 -> rolling_acc 71.1% (n=90, healthy) | win=250 -> 67.8% (healthy)
# 两窗都在 0.65 之上, 故健康线保持 0.65(余量 ~6pp), 0.55 预警线远低, 无需重定。
ALERT_THRESHOLD = 0.55  # 衰减告警线
HEALTHY_BASELINE = 0.65


def evaluate() -> dict:
    from app.data import store
    from app.data.moex_rates import load_moex
    store.init_db()
    df = store.load_rates()
    off_d = [d.strftime("%Y-%m-%d") for d in df.index]
    lp = np.log(df["cny_rub"].to_numpy(float))
    m = len(lp)
    idx_of = {d: i for i, d in enumerate(off_d)}
    moex = load_moex()
    common = [d for d in off_d if d in moex]
    pos = np.array([idx_of[d] for d in common])
    rawdev = np.array([np.log(moex[d]) - lp[idx_of[d]] for d in common])

    # 滚动标准化 zdev(无前视)。窗口必须与生产模型一致(config.ZDEV_WINDOW),
    # 否则这里评估的是另一个信号, 健康度卡片会失真。
    z = np.full(len(common), np.nan)
    for k in range(len(common)):
        h = rawdev[:k + 1]
        if len(h) >= 60:
            a = h[-config.ZDEV_WINDOW:]
            z[k] = (rawdev[k] - a.mean()) / (a.std() + 1e-9)

    # 只评估"已实现"的 7 日预测(i+N < m), 取最近 WINDOW_DAYS 个
    ev = []
    for k in range(len(common)):
        i = pos[k]
        if np.isnan(z[k]) or i + N_HOR >= m:
            continue
        pred = 1 if z[k] > 0 else 0
        actual = 1 if lp[i + N_HOR] > lp[i] else 0
        ev.append((off_d[i], pred == actual))
    recent = ev[-WINDOW_DAYS:]
    if not recent:
        return {"status": "no_data", "alert": False}
    acc = sum(1 for _, hit in recent if hit) / len(recent)
    status = "healthy" if acc >= HEALTHY_BASELINE else (
        "degraded" if acc >= ALERT_THRESHOLD else "alert")
    return {
        "rolling_acc": round(acc, 4),
        "n": len(recent),
        "window_days": WINDOW_DAYS,
        "healthy_baseline": HEALTHY_BASELINE,
        "alert_threshold": ALERT_THRESHOLD,
        "last_eval_date": recent[-1][0],
        "status": status,
        "alert": acc < ALERT_THRESHOLD,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = evaluate()
    print(json.dumps(r, ensure_ascii=False, indent=2))
