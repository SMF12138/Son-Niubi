"""极端均值回复信号(MeanRevSignal)。

发现(2026-09-09 严格滚动回测,无前视):
- 当 CNY/RUB 对 20 日均线的偏离 |dev20| >= 2×滚动相对波动率(250日窗) 时,
  未来 30/60 日方向精度达 77-78%(覆盖率约 4%)。
- 方向: 价格远高于 MA20 → 预期回落(跌); 远低于 MA20 -> 预期回升(涨)。
- "远低于 MA20"(低偏离)方向在 N=60 特别强(90%+, 62 样本)。

用法:
    from app.models.mean_reversion import mean_rev_signal
    sig = mean_rev_signal(close, i, N, k=2.0)  # -> {'active':True/False,'pred':0/1,'strength':..}

所有量只用 <= i 的数据计算(滚动 MA20 / 滚动 250 日相对波动率),严格无前视。
"""
import numpy as np


def _rolling_mean_std(x: np.ndarray, ma_win: int = 20, vol_win: int = 250):
    """返回 (ma20, dev20, rel_sd): 每次只用 <=t 的数据。"""
    m = len(x)
    ma = np.full(m, np.nan)
    dev = np.full(m, np.nan)
    rel_sd = np.full(m, np.nan)
    # 高德: t 时刻的 MA20 用 [t-19..t]
    for t in range(ma_win - 1, m):
        w_ma = x[t - ma_win + 1: t + 1]
        ma[t] = w_ma.mean()
        lo = max(0, t - vol_win + 1)
        w_v = x[lo: t + 1]
        m_v = w_v.mean()
        if m_v > 0 and len(w_v) > 30:
            rel_sd[t] = w_v.std() / m_v
    dev = (x - ma) / ma
    return ma, dev, rel_sd


def mean_rev_signal(close: np.ndarray, i: int, N: int,
                    k: float = 2.0) -> dict:
    """在时间点 i 判断极端均值回复信号。

    返回:
      trigger: True 当 |dev20| >= k*rel_sd
      pred: 1=涨 0=跌(仅 trigger 时有意义)
      dev: 当前偏离度(相对)
      rel_sd: 当前滚动波动率
    """
    _, dev, rel_sd = _rolling_mean_std(close)
    d = dev[i]
    s = rel_sd[i]
    if not np.isfinite(d) or not np.isfinite(s) or s <= 0:
        return {"trigger": False, "pred": None, "dev": None, "strength": 0.0}
    triggered = abs(d) >= k * s
    pred = 0 if d > 0 else 1  # 高偏离→跌, 低偏离→涨
    return {"trigger": triggered, "pred": pred, "dev": float(d),
            "strength": float(abs(d) / s if s else 0.0)}


# 便捷: 直接用 numpy 向量化替代逐点, 供回测批量使用
def mean_rev_series(close: np.ndarray, N: int, k: float = 2.0,
                    ma_win: int = 20, vol_win: int = 250):
    """批量版: 返回 dict{i: {trigger,pred,strength}} 只含触发点。"""
    m = len(close)
    ma = np.full(m, np.nan)
    dev = np.full(m, np.nan)
    rel_sd = np.full(m, np.nan)
    for t in range(ma_win - 1, m):
        ma[t] = close[t - ma_win + 1: t + 1].mean()
        lo = max(0, t - vol_win + 1)
        w_v = close[lo: t + 1]
        if len(w_v) > 30:
            rel_sd[t] = w_v.std() / w_v.mean()
    dev = (close - ma) / ma
    out = {}
    for i in range(m):
        d = dev[i]
        s = rel_sd[i]
        if not np.isfinite(d) or not np.isfinite(s) or s <= 0:
            continue
        if abs(d) >= k * s:
            out[i] = {"pred": 0 if d > 0 else 1,
                      "dev": float(d), "strength": float(abs(d) / s)}
    return out

def synthetic_meanrev_score(Xf, valid, i, feat_cols, win=500):
    """
    合成均值回复分数(滚动标准化, 无前视)。
    分量: ma60_dev, ma20_dev, mom20, pos60, cusd_mom20 (全部负向 = 看涨)。
    score > 0 -> 看涨; |score| 越大越强。
    只用 Xf 的 <=i 历史行做均值/方差, 严格无泄漏。
    返回 dict {score, pred, strength}
    """
    import numpy as np
    hist = valid[valid <= i]
    if len(hist) < 200:
        return {"score": 0.0, "pred": 0, "strength": 0.0}
    hist = hist[-win:]
    Xi = Xf[hist][:, feat_cols].astype(float)
    mu = np.nanmean(Xi, 0); sd = np.nanstd(Xi, 0) + 1e-9
    xnow = Xf[i, feat_cols]
    if np.any(np.isnan(xnow)):
        return {"score": 0.0, "pred": 0, "strength": 0.0}
    z = (xnow - mu) / sd
    score = float(-np.mean(z))   # 负向 → 看涨 → 负(原始特征)转正
    return {"score": score, "pred": 1 if score > 0 else 0,
            "strength": float(abs(score))}
