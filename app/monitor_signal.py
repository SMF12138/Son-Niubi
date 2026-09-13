"""全周期信号健康监控。

每日计算各周期策略最近窗口的已实现命中率, 与基准对比。
衰减到告警线以下时输出告警, 让用户知道该周期信号可能失效。

覆盖: 7日(MOEX价差) / 30日(moex_z) / 60日(pure_rev) / 90日(extreme_dist)
用法: python -m app.monitor_signal
输出 JSON: {horizons: {7:{...}, 30:{...}, ...}, overall_status}
"""
import json
import logging

import numpy as np
import pandas as pd

from app import config

log = logging.getLogger(__name__)

# 各周期健康基准(2021后OOS命中率)与告警线。
# window 统一为"最近1年(365天)内已兑现的预测", 确保各周期评估时段可比。
HORIZON_CONFIG = {
    7:  {"healthy": 0.65, "alert": 0.55, "name": "MOEX价差"},
    30: {"healthy": 0.58, "alert": 0.50, "name": "MOEX_z确认闸门"},
    60: {"healthy": 0.59, "alert": 0.48, "name": "均值回复"},
    90: {"healthy": 0.70, "alert": 0.50, "name": "极值距离"},
}
WINDOW_DAYS = 365


def _longhorizon_preds(df, N, oil_df=None):
    """用当前生产策略计算每个 t 的预测方向。返回 [(date, hit)] 仅已实现的。

    30 日 moex_z 与生产完全同口径: MOEX 日须过 C1/C2/C4≥2 确认闸门才计入,
    确认不足的弱档日不发声(与 longhorizon.predict_longhorizon 一致)。
    """
    from app.models.longhorizon import (
        HORIZON_POLICY, compute_long_signals, _aligned_moex,
        compute_extreme_signals, compute_moex_confirms,
        brent_ret20_aligned, GATE_MIN_CONFIRMS, WARMUP,
    )
    from app.data.moex_rates import load_moex
    p = df["cny_rub"].to_numpy(float)
    lp = np.log(p)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    moex_close = _aligned_moex(dates, load_moex())
    sig = compute_long_signals(lp, moex_close)
    rev, z, has_moex = sig["rev"], sig["z"], sig["has_moex"]
    gate = compute_moex_confirms(lp, moex_close, z,
                                 brent_ret20_aligned(df.index, oil_df))
    ext = compute_extreme_signals(p)
    bo, ed = ext["breakout"], ext["extreme_dist"]
    policy = HORIZON_POLICY.get(N, "pure_rev")

    out = []
    for t in range(WARMUP, len(p) - N):
        if policy == "pure_rev":
            pred = 1 if rev[t] > 0 else 0
        elif policy == "moex_z":
            if has_moex[t] and np.isfinite(z[t]):
                # 与生产同闸门: 确认不足的日子不发声, 不计入健康命中率
                if gate[t] < GATE_MIN_CONFIRMS:
                    continue
                pred = 1 if z[t] > 0 else 0
            else:
                pred = 1 if rev[t] > 0 else 0
        elif policy == "breakout":
            if not np.isfinite(bo[t]):
                continue
            pred = int(bo[t])
        elif policy == "extreme_dist":
            if not np.isfinite(ed[t]):
                continue
            pred = int(ed[t])
        else:
            continue
        actual = 1 if p[t + N] > p[t] else 0
        out.append((dates[t], pred == actual))
    return out


def _moex7_preds(df):
    """7日MOEX价差信号。"""
    from app.data.moex_rates import load_moex
    lp = np.log(df["cny_rub"].to_numpy(float))
    off_d = [d.strftime("%Y-%m-%d") for d in df.index]
    m = len(lp)
    idx_of = {d: i for i, d in enumerate(off_d)}
    moex = load_moex()
    common = [d for d in off_d if d in moex]
    pos = np.array([idx_of[d] for d in common])
    rawdev = np.array([np.log(moex[d]) - lp[idx_of[d]] for d in common])
    z = np.full(len(common), np.nan)
    for k in range(len(common)):
        h = rawdev[:k + 1]
        if len(h) >= 60:
            a = h[-config.ZDEV_WINDOW:]
            z[k] = (rawdev[k] - a.mean()) / (a.std() + 1e-9)
    ev = []
    for k in range(len(common)):
        i = pos[k]
        if np.isnan(z[k]) or i + 7 >= m:
            continue
        pred = 1 if z[k] > 0 else 0
        actual = 1 if lp[i + 7] > lp[i] else 0
        ev.append((off_d[i], pred == actual))
    return ev


def evaluate() -> dict:
    from app.data import store
    store.init_db()
    df = store.load_rates()

    horizons = {}
    today = df.index[-1]
    cutoff = today - pd.Timedelta(days=WINDOW_DAYS)
    oil_df = store.load_oil()
    for N, cfg in HORIZON_CONFIG.items():
        if N == 7:
            ev = _moex7_preds(df)
        else:
            ev = _longhorizon_preds(df, N, oil_df=oil_df)
        # 只取最近1年内发出的预测(已兑现的)
        recent = [(d, hit) for d, hit in ev
                  if pd.Timestamp(d) >= cutoff]
        if not recent:
            horizons[str(N)] = {"status": "no_data", "alert": False}
            continue
        acc = sum(1 for _, hit in recent if hit) / len(recent)
        status = "healthy" if acc >= cfg["healthy"] else (
            "degraded" if acc >= cfg["alert"] else "alert")
        horizons[str(N)] = {
            "name": cfg["name"],
            "rolling_acc": round(acc, 4),
            "n": len(recent),
            "window_days": WINDOW_DAYS,
            "healthy_baseline": cfg["healthy"],
            "alert_threshold": cfg["alert"],
            "last_eval_date": recent[-1][0],
            "first_eval_date": recent[0][0],
            "status": status,
            "alert": acc < cfg["alert"],
        }

    any_alert = any(h.get("alert") for h in horizons.values())
    any_degraded = any(h.get("status") == "degraded" for h in horizons.values())
    overall = "alert" if any_alert else ("degraded" if any_degraded else "healthy")
    return {"horizons": horizons, "overall_status": overall,
            "any_alert": any_alert}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = evaluate()
    print(json.dumps(r, ensure_ascii=False, indent=2))
