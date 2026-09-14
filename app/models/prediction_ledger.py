"""预测永久留档 + 影子策略 + 滚动复盘(升级清单第 9/10/14 条)。

记录什么:
  - 生产四周期(7/30/60/90): 每次 save_forecasts 追加, 同日幂等(UNIQUE 键)。
    中性/拒答日 prediction=NULL 也留档 —— "不预测"本身是要被评估的决策。
  - 影子策略(只记录不发声, 纯前瞻 OOS 对照, 禁止拿回测数字替代):
      shadow-30d-z-nogate : 30 日纯 z, 不装≥2 闸门(检验闸门的真实增量)
      shadow-60d-edist    : 60 日极值距离(检验是否该替换纯 rev)
      shadow-60d-resonance: 60 日 rev∩30闸门∩90极值 三方同向才发声
                            (回测 67.2% 但 2025 年 50.5%, 交给前瞻裁决)
到期回填: as_of + N 个交易日的真实价实现后, 写 realized/realized_ret。
"""
import json
import logging

import numpy as np

from app import config
from app.data import store

log = logging.getLogger(__name__)

SHADOW_30_NO_GATE = "shadow-30d-z-nogate"
SHADOW_60_EDIST = "shadow-60d-edist"
SHADOW_60_RESONANCE = "shadow-60d-resonance"
_REVIEW_WINDOWS = (20, 50, 100)


def _meta(dr: dict) -> str:
    """方向字典整体存档(含 z/桶/确认数/闸门/溯源), 供日后错误类型分析。"""
    keep = {k: v for k, v in dr.items()
            if k not in ("prob_up", "prob_down", "forecast")}
    return json.dumps(keep, ensure_ascii=False, default=str)


def _shadow_rows(as_of: str, df, oil_df, moex_map=None) -> list[dict]:
    """计算数据末日各影子策略的方向。无信号的策略当日不产生记录。
    moex_map 仅测试注入用; 生产默认从库加载。"""
    from app.models.longhorizon import (
        compute_long_signals, _aligned_moex, compute_extreme_signals,
        compute_moex_confirms, brent_ret20_aligned, GATE_MIN_CONFIRMS,
    )
    if moex_map is None:
        from app.data.moex_rates import load_moex
        moex_map = load_moex()

    lp = np.log(df["cny_rub"].to_numpy(float))
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    t = len(lp) - 1
    moex = _aligned_moex(dates, moex_map)
    sig = compute_long_signals(lp, moex)
    rev, z, has_moex = sig["rev"], sig["z"], sig["has_moex"]
    gate = compute_moex_confirms(lp, moex, z, brent_ret20_aligned(df.index, oil_df))
    ed = compute_extreme_signals(df["cny_rub"].to_numpy(float))["extreme_dist"]

    def row(version, horizon, pred, meta):
        return {"as_of": as_of, "horizon": horizon, "model_version": version,
                "is_shadow": 1, "prediction": pred, "confidence": None,
                "meta": json.dumps(meta, ensure_ascii=False, default=str)}

    rows = []
    # 30 日纯 z 无闸门: MOEX 日按 z 符号, 无 MOEX 日 rev 兜底(与生产兜底一致)
    if has_moex[t] and np.isfinite(z[t]):
        rows.append(row(SHADOW_30_NO_GATE, 30,
                        1 if z[t] > 0 else 0, {"z": round(float(z[t]), 4)}))
    elif np.isfinite(rev[t]):
        rows.append(row(SHADOW_30_NO_GATE, 30,
                        1 if rev[t] > 0 else 0, {"fallback": "rev"}))
    # 60 日极值距离
    if np.isfinite(ed[t]):
        rows.append(row(SHADOW_60_EDIST, 60, int(ed[t]),
                        {"extreme_dist": round(float(ed[t]), 4)}))
    # 60 日期限共振: rev 与(过闸的)30日z 与 90日极值三方同向
    gated_z = (1 if z[t] > 0 else 0) if (
        has_moex[t] and np.isfinite(z[t]) and gate[t] >= GATE_MIN_CONFIRMS) else None
    rev_dir = 1 if rev[t] > 0 else 0 if np.isfinite(rev[t]) else None
    ed_dir = int(ed[t]) if np.isfinite(ed[t]) else None
    if gated_z is not None and rev_dir is not None and ed_dir is not None \
            and rev_dir == gated_z == ed_dir:
        rows.append(row(SHADOW_60_RESONANCE, 60, rev_dir,
                        {"rev_dir": rev_dir, "z_dir": gated_z,
                         "ed_dir": ed_dir, "gate": int(gate[t])}))
    return rows


def _settle_pending(df) -> int:
    """用真实价回填所有到期记录。prediction=NULL(中性拒答)不参与命中统计。"""
    lp = np.log(df["cny_rub"].to_numpy(float))
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    pos = {d: i for i, d in enumerate(dates)}
    n_settled = 0
    for r in store.pending_predictions():
        p = pos.get(r["as_of"])
        n = r["horizon"]
        if p is None or p + n > len(lp) - 1 or r["prediction"] is None:
            continue
        ret = float(lp[p + n] - lp[p])
        store.mark_realized(r["id"], 1 if ret > 0 else 0, ret, dates[p + n])
        n_settled += 1
    return n_settled


def record_forecast_day(df, drs: dict, oil_df=None, moex_map=None) -> dict:
    """追加当日生产预测+影子预测并结算到期记录。任何异常由调用方兜底,
    绝不允许留档故障影响预测写盘。"""
    as_of = df.index[-1].date().isoformat()
    rows = [
        {"as_of": as_of, "horizon": int(N), "model_version": config.MODEL_VERSION,
         "is_shadow": 0, "prediction": dr.get("prediction"),
         "confidence": dr.get("confidence"), "meta": _meta(dr)}
        for N, dr in sorted(drs.items())
    ]
    try:
        rows.extend(_shadow_rows(as_of, df, oil_df, moex_map=moex_map))
    except Exception as e:  # noqa: BLE001 影子失败不影响生产留档
        log.warning("shadow prediction failed: %s", e)
    inserted = store.insert_predictions(rows)
    settled = _settle_pending(df)
    return {"inserted": inserted, "settled": settled}


def review(windows=_REVIEW_WINDOWS) -> dict:
    """最近 20/50/100 次(及全部)已兑现发声的滚动命中率, 按周期×模型版本。
    尚未有任何兑现记录的键也会出现(n=0), 让留档累积过程可见。"""
    out = {}
    full = store.load_ledger(only_realized=False)
    if full.empty:
        return {"models": out}
    realized = full[full["realized"].notna() & full["prediction"].notna()].copy()
    if len(realized):
        realized["hit"] = (realized["prediction"].astype(int)
                           == realized["realized"].astype(int))
    for (horizon, ver), g0 in full.groupby(["horizon", "model_version"]):
        key = f"{horizon}:{ver}"
        stats = {}
        if len(realized):
            g = realized[(realized["horizon"] == horizon)
                         & (realized["model_version"] == ver)]
            g = g.sort_values("as_of", ascending=False)
        else:
            g = realized
        for k in list(windows) + [None]:
            sub = g if k is None else g.head(k)
            label = "all" if k is None else str(k)
            stats[label] = {"n": int(len(sub)),
                            "hit": round(float(sub["hit"].mean()), 4) if len(sub) else None}
        out[key] = {
            "horizon": int(horizon), "model_version": ver,
            "is_shadow": int(g0["is_shadow"].iloc[0]), "windows": stats,
            "first_as_of": g0["as_of"].min(), "last_as_of": g0["as_of"].max(),
            "neutral_days": int(g0["prediction"].isna().sum()),
            "pending_days": int(
                (g0["realized"].isna() & g0["prediction"].notna()).sum()),
        }
    return {"models": out}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    store.init_db()
    print(json.dumps(review(), ensure_ascii=False, indent=1))
