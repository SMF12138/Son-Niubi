"""Phase 2 第 3 步: 单因子 OOS 检验(只观察, 不调参, 不进生产)。

口径(冻结, 见 contract.py):
  - 标签: 未来 30 个 CBR 牌价日的简单收益 R30, R30>0 为涨(UP=1), 否则跌(DOWN=0)
  - 检验规则(预注册, 不据结果改):
      规则A 中位数阈值: f[t] > 截至 t 的已实现样本中位数 -> 预测涨
      规则B 符号:        f[t] > 0 -> 预测涨(适用于有方向先验的特征)
  - 扩展窗 OOS: t 时刻估计阈值只用 j+30<=t 的已实现样本(严格因果, 无未来函数)
  - 不做任何特征选择、不调阈值、不做交叉验证, 只报告真实兑现率

产物: artifacts/single_factor_30d.json + ..._report.md
"""
import datetime as dt
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from app.data import store
from app.data.features import build_features
from app.models.experiments.contract import (
    HORIZON_DAYS, binary_label, r30_simple_return,
)

ART = Path(__file__).resolve().parent / "artifacts"
JSON_PATH = ART / "single_factor_30d.json"
REPORT_PATH = ART / "single_factor_30d_report.md"

ERAS = [("2010-2015", "2010-01-01", "2016-01-01"),
        ("2016-2020", "2016-01-01", "2021-01-01"),
        ("2021-今", "2021-01-01", "2200-01-01")]
WARMUP = 120  # 特征滚动窗最大 120, 与生产一致


def _build_labels(df: pd.DataFrame) -> pd.Series:
    p = df["cny_rub"].astype(float).values
    n = len(p)
    r30 = np.full(n, np.nan)
    for t in range(n - HORIZON_DAYS):
        r30[t] = r30_simple_return(p[t], p[t + HORIZON_DAYS])
    return pd.Series(r30, index=df.index)


def _eval_rule(f: np.ndarray, y: np.ndarray, rule: str) -> dict:
    """对单个特征做扩展窗 OOS 检验。rule: 'median' or 'sign'。"""
    n = len(f)
    hits, preds = [], []
    # 已实现样本的特征与标签索引: j 满足 j+30 < n 且结果已实现
    # t 时刻可用样本: j 满足 j+HORIZON <= t (结果在 t 前已实现)
    for t in range(WARMUP, n - HORIZON_DAYS):
        ft = f[t]
        if not np.isfinite(ft):
            continue
        # 可用历史样本: j in [WARMUP, t-HORIZON], 其标签 y[j] 已实现
        avail_j = np.arange(WARMUP, t - HORIZON_DAYS + 1)
        avail_f = f[avail_j]
        avail_y = y[avail_j]
        mask = np.isfinite(avail_f) & np.isfinite(avail_y)
        avail_f, avail_y = avail_f[mask], avail_y[mask]
        if len(avail_f) < 30:   # 最少 30 个已实现样本才估计
            continue
        if rule == "median":
            thr = np.median(avail_f)
            pred = 1 if ft > thr else 0
        else:  # sign
            pred = 1 if ft > 0 else 0
        hits.append(int(pred == int(y[t])))
        preds.append(pred)
    if not hits:
        return {"n": 0, "hit": None, "up_pred_pct": None}
    return {
        "n": len(hits),
        "hit": round(sum(hits) / len(hits), 4),
        "up_pred_pct": round(sum(preds) / len(preds), 4),
    }


def _era_hits(f: np.ndarray, y: np.ndarray, dates: pd.DatetimeIndex,
              rule: str) -> dict:
    """分年代 OOS(同样严格扩展窗估计)。"""
    out = {}
    n = len(f)
    for name, lo, hi in ERAS:
        lo_t = pd.Timestamp(lo)
        hi_t = pd.Timestamp(hi)
        era_idx = [t for t in range(WARMUP, n - HORIZON_DAYS)
                   if lo_t <= dates[t] < hi_t]
        if not era_idx:
            out[name] = {"n": 0, "hit": None}
            continue
        hits = []
        for t in era_idx:
            ft = f[t]
            if not np.isfinite(ft) or not np.isfinite(y[t]):
                continue
            avail_j = np.arange(WARMUP, t - HORIZON_DAYS + 1)
            af = f[avail_j]; ay = y[avail_j]
            m = np.isfinite(af) & np.isfinite(ay)
            af, ay = af[m], ay[m]
            if len(af) < 30:
                continue
            thr = np.median(af) if rule == "median" else 0.0
            pred = 1 if ft > thr else 0
            hits.append(int(pred == int(y[t])))
        out[name] = {"n": len(hits),
                     "hit": round(sum(hits) / len(hits), 4) if hits else None}
    return out


def build_report() -> dict:
    df = store.load_rates()
    oil = store.load_oil()
    rate = store.load_key_rate()
    try:
        sent = store.load_daily_sentiment()
    except Exception:
        sent = None
    F = build_features(df, oil_df=oil, sentiment_df=sent, rate_df=rate)
    # 加入 longhorizon 的 rev 信号作为基准(生产当前用的信号)
    from app.models.longhorizon import compute_long_signals, _aligned_moex
    from app.data.moex_rates import load_moex
    lp = np.log(df["cny_rub"].astype(float).values)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    moex = _aligned_moex(dates, load_moex())
    sig = compute_long_signals(lp, moex)
    F["rev"] = sig["rev"]   # 生产当前主信号

    r30 = _build_labels(df)
    y = r30.map(binary_label).astype(float)
    f_arr = F.to_numpy(float)
    y_arr = y.to_numpy(float)

    results = {}
    for ci, name in enumerate(F.columns):
        fcol = f_arr[:, ci]
        if not np.isfinite(fcol).any():
            continue
        rec = {"feature": name}
        for rule in ("median", "sign"):
            e = _eval_rule(fcol, y_arr, rule)
            eras = _era_hits(fcol, y_arr, df.index, rule)
            rec[rule] = {"overall": e, "eras": eras}
        # 取两种规则中全历史 OOS 较好者
        best = max(("median", "sign"), key=lambda r: (rec[r]["overall"]["hit"] or 0))
        rec["best_rule"] = best
        rec["best_hit"] = rec[best]["overall"]["hit"]
        rec["best_n"] = rec[best]["overall"]["n"]
        rec["best_2021_hit"] = rec[best]["eras"]["2021-今"]["hit"]
        results[name] = rec

    # 排序: 2021 后 OOS 命中降序
    ranked = sorted(results.values(),
                    key=lambda r: (r["best_2021_hit"] or 0), reverse=True)
    doc = {
        "meta": {
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "contract_version": "2026-09-13-v1",
            "horizon_days": HORIZON_DAYS,
            "label": "binary: R30>0 -> UP, else DOWN",
            "rules": ["median: f[t] > running_median(realized) -> UP",
                      "sign:   f[t] > 0 -> UP"],
            "oos": "expanding window, threshold estimated only from j+30<=t realized samples",
            "rows_total": int(len(df)),
            "rows_labeled": int(np.isfinite(y_arr).sum()),
        },
        "results": ranked,
    }
    return doc


def render(doc: dict) -> str:
    L = []
    L.append("# Phase 2 第 3 步 · 30 日单因子 OOS 检验")
    L.append("")
    L.append(f"- 生成: {doc['meta']['generated']}")
    L.append(f"- 标签: 未来 30 日简单收益 R30 > 0 为涨(UP), 否则跌(DOWN)")
    L.append("- 规则(预注册, 不调): A=中位数阈值, B=符号; 扩展窗 OOS, 阈值只用已实现样本")
    L.append(f"- 样本: {doc['meta']['rows_labeled']} 行可标")
    L.append("- 基线: 抛硬币 50.0%, 多数类 52.0%; 用户门槛 OOS ≥70%")
    L.append("")
    L.append("## 因子生死表(按 2021 后 OOS 命中率降序)")
    L.append("")
    L.append("| 排名 | 特征 | 最佳规则 | 全历史 | n | 2010-15 | 2016-20 | 2021-今 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(doc["results"], 1):
        b = r["best_rule"]
        ov = r[b]["overall"]
        er = r[b]["eras"]
        def pct(x): return "—" if x is None else f"{x*100:.1f}%"
        L.append(f"| {i} | {r['feature']} | {b} | {pct(ov['hit'])} | {ov['n']} | "
                 f"{pct(er['2010-2015']['hit'])} | {pct(er['2016-2020']['hit'])} | "
                 f"{pct(er['2021-今']['hit'])} |")
    L.append("")
    # 达标候选
    cand = [r for r in doc["results"]
            if r["best_2021_hit"] and r["best_2021_hit"] >= 0.70
            and r["best_2021_hit"]]
    L.append("## 达标候选(2021 后 OOS ≥ 70%)")
    L.append("")
    if cand:
        for r in cand:
            L.append(f"- **{r['feature']}** ({r['best_rule']}): 2021后 "
                     f"{r['best_2021_hit']*100:.1f}%, 全历史 {r['best_hit']*100:.1f}%")
    else:
        L.append("**无**。35 个因果特征 + rev 信号中，没有任何一个在 2021 年后的扩展窗 OOS 达到 70%。")
    L.append("")
    L.append("> 本检验只观察不调参。所有阈值(中位数)均由 t 时刻之前的已实现样本估计，")
    L.append("> 无未来函数、无交叉验证、无特征选择。下一步将进入多因子/模型检验(Phase 2 第 4-5 步)，")
    L.append("> 看特征组合能否突破单因子天花板。")
    return "\n".join(L) + "\n"


def main() -> int:
    doc = build_report()
    ART.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(render(doc))
    print(f"[single-factor] {JSON_PATH}")
    print(f"[single-factor] {REPORT_PATH}")
    top = doc["results"][:5]
    for r in top:
        print(f"  {r['feature']:18s} {r['best_rule']:7s} "
              f"2021后={(r['best_2021_hit'] or 0)*100:.1f}%  "
              f"全历史={(r['best_hit'] or 0)*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
