"""Phase 2 实验: 基于区间极值(最大/最小值)的新算法探索。

只在 7/30/60/90 维度。严格扩展窗 OOS, 不用未来数据。
所有高低点均由 t 及之前的 rolling window 计算。

算法设计(全部基于区间极值):
  A. 区间位置均值回复: pos = (P-low)/(high-low), pos<0.2 看涨, pos>0.8 看跌
  B. 极值突破(动量): 创N日新高看涨, 创N日新低看跌
  C. 极值距离比: 更接近N日最低看涨, 更接近最高看跌
  D. 高低点趋势: high_N和low_N的20日斜率同向决定方向
  E. 双区间共振: 短期窗(N/4)和长期窗(N)区间位置同向才发声
"""
import datetime as dt, json
from pathlib import Path
import numpy as np, pandas as pd
from app.data.store import load_rates

ART = Path(__file__).resolve().parent / "artifacts"
WARMUP = 120
HORIZONS = [7, 30, 60, 90]


def _rolling(x, w, fn):
    s = pd.Series(x)
    return s.rolling(w, min_periods=w).apply(fn, raw=True).to_numpy()


def algo_pos_meanrevert(p, lookback, t):
    """A: 区间位置均值回复。pos<0.2看涨, pos>0.8看跌。"""
    lo = np.min(p[max(0,t-lookback+1):t+1])
    hi = np.max(p[max(0,t-lookback+1):t+1])
    if hi == lo:
        return None
    pos = (p[t] - lo) / (hi - lo)
    if pos < 0.2:
        return 1
    if pos > 0.8:
        return 0
    return None


def algo_breakout(p, lookback, t):
    """B: 极值突破。创N日新高看涨, 新低看跌。"""
    lo = np.min(p[max(0,t-lookback):t])   # 不含当日
    hi = np.max(p[max(0,t-lookback):t])
    if p[t] > hi:
        return 1
    if p[t] < lo:
        return 0
    return None


def algo_extreme_dist(p, lookback, t):
    """C: 极值距离比。距最低更近看涨, 距最高更近看跌。"""
    lo = np.min(p[max(0,t-lookback+1):t+1])
    hi = np.max(p[max(0,t-lookback+1):t+1])
    if hi == lo:
        return None
    dist_low = p[t] - lo
    dist_high = hi - p[t]
    return 1 if dist_low < dist_high else 0


def algo_hl_trend(p, lookback, t):
    """D: 高低点趋势。high_N和low_N的短期斜率同向。"""
    if t < lookback + 20:
        return None
    hi_series = [np.max(p[max(0,i-lookback+1):i+1]) for i in range(t-20, t+1)]
    lo_series = [np.min(p[max(0,i-lookback+1):i+1]) for i in range(t-20, t+1)]
    hi_slope = hi_series[-1] - hi_series[0]
    lo_slope = lo_series[-1] - lo_series[0]
    if hi_slope > 0 and lo_slope > 0:
        return 1
    if hi_slope < 0 and lo_slope < 0:
        return 0
    return None


def algo_dual_range(p, lookback, t):
    """E: 双区间共振。短期和长期区间位置同向。"""
    short_w = max(5, lookback // 4)
    def pos(w):
        lo = np.min(p[max(0,t-w+1):t+1])
        hi = np.max(p[max(0,t-w+1):t+1])
        return (p[t]-lo)/(hi-lo) if hi!=lo else 0.5
    ps = pos(short_w); pl = pos(lookback)
    if ps < 0.3 and pl < 0.3:
        return 1
    if ps > 0.7 and pl > 0.7:
        return 0
    return None


ALGOS = {
    "A_pos_mr": algo_pos_meanrevert,
    "B_breakout": algo_breakout,
    "C_extreme_dist": algo_extreme_dist,
    "D_hl_trend": algo_hl_trend,
    "E_dual_range": algo_dual_range,
}


def eval_algo(p, N, algo_fn, lookback=None):
    """扩展窗 OOS。lookback 默认 = N。"""
    lb = lookback or N
    hits, n = [], 0
    hits_m, n_m = [], 0
    for t in range(WARMUP, len(p) - N):
        pred = algo_fn(p, lb, t)
        if pred is None:
            continue
        actual = 1 if p[t+N] > p[t] else 0
        h = int(pred == actual)
        hits.append(h); n += 1
        if t >= len(p) * 0.6:   # 约 2021 后(粗略)
            hits_m.append(h); n_m += 1
    return (sum(hits)/n if n else None, n,
            sum(hits_m)/n_m if n_m else None, n_m)


def main():
    df = load_rates()
    p = df["cny_rub"].astype(float).values
    results = {}
    for N in HORIZONS:
        results[str(N)] = {}
        for name, fn in ALGOS.items():
            h, n, h21, n21 = eval_algo(p, N, fn)
            results[str(N)][name] = {
                "hit": round(h, 4) if h else None,
                "n": n,
                "hit_2021": round(h21, 4) if h21 else None,
                "n_2021": n21,
                "coverage": round(n / max(1, len(p) - N - WARMUP), 3),
            }

    doc = {
        "meta": {"generated": dt.datetime.now().isoformat(timespec="seconds"),
                 "horizons": HORIZONS,
                 "algos": list(ALGOS.keys()),
                 "note": "lookback=N, 严格因果 rolling window"},
        "results": results,
    }
    ART.mkdir(parents=True, exist_ok=True)
    with open(ART / "extreme_algos_30d.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    # 打印
    print(f"{'周期':<6}{'算法':<18}{'全历史':<10}{'n':<8}{'2021后':<10}{'覆盖':<8}")
    print("-"*60)
    for N in HORIZONS:
        for name in ALGOS:
            r = results[str(N)][name]
            h = f"{r['hit']*100:.1f}%" if r['hit'] else "—"
            h21 = f"{r['hit_2021']*100:.1f}%" if r['hit_2021'] else "—"
            print(f"N={N:<4}{name:<18}{h:<10}{r['n']:<8}{h21:<10}{r['coverage']*100:.0f}%")
    print(f"\n产物: {ART/'extreme_algos_30d.json'}")


if __name__ == "__main__":
    main()
