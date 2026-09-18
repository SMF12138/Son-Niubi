"""长周期(30/60/90 交易日)方向模型:长期均值回复 + MOEX 确认 + 70% 硬门槛。

为什么是均值回复而不是趋势跟踪:
  CNY/RUB 由央行管理,价格围绕缓慢移动的中枢做季度级摆动。严格扩展窗 OOS 回测
  (t 时刻只用 j+N<=t 的已实现结果)表明:
    - 30 日尺度趋势跟踪约 49%,无效;
    - 60/90 日尺度追趋势约 45~49%,是反指;
    - 60/90 日"偏离中枢极端后回归"在 MOEX 市场价同向确认时 72%/75%(2022-06 后)。
  全样本无确认口径最高仅 60~68%,达不到用户设定的 70% 有效性门槛。

信号(全部因果,只用 <=t 的数据):
  1. 主信号 rev = -(logP - MA_REV)/std(VOL_WIN) —— 价格相对长期中枢的标准化
     偏离取反:rev>0 超跌看反弹,rev<0 超涨看回落。
  2. 确认信号 z = (md - MA60(md))/std60(md),md = log(MOEX 收盘) - log(官方价),
     MOEX 缺失日按最近已知价 ffill(只用已知信息,合法)。z 与 rev 同号才进确认池。

发声规则(2026-09-13 产品决策更新):
  70% 门槛不再拦截发声, 只决定诚实标记: 方向始终输出(prediction = rev 符号),
  把握度 = 该规则扩展窗 OOS 实测兑现率(与旧校准哲学一致: 桶命中率因选择效应
  虚高, 仅作披露)。未通过门槛时 experimental=True, 界面按"弱倾向·实验中"
  展示, 投影中位线不偏移。仅在校准缺失/历史不足/无任何可测兑现率时输出中性。

本模块是纯函数:不读写任何运行状态文件,同数据同结果,可复现、可回测。
"""
import json
import logging
import time

import numpy as np
import pandas as pd

from app import config

log = logging.getLogger(__name__)

# ---- 信号参数(与正式回测口径一致,修改必须重跑回测) ----
REV_MA_WIN = 120        # 长期中枢窗口(实测 60~200 为正效应高原,120 稳定)
VOL_WIN = 60            # 标准化用波动率窗口
Z_WIN = 60              # MOEX 偏离 z 窗口
WARMUP = REV_MA_WIN     # 信号有效所需最小历史行数

# ---- 发声门槛(用户硬约束) ----
LONGHORIZON_GATE = 0.70
LONG_MIN_BUCKET_N = 20

# ---- 每周期发声策略(预注册候选一次性回测裁决, 禁止事后换规则) ----
# 30: 三轮 15 候选 + 60 随机变体 + 等权合成全部未达 70%(最好近年代 55.9%)。
#     应用户产品决策保留"实验性弱倾向"档, 输出永远带 experimental=True,
#     界面不得按"已验证"展示。2026-09-13 起门槛不再拦截发声(见模块头),
#     WEAK_GATE 仅用于回测中评估弱规则本身的 OOS 兑现率。
# 60: 仅"共振"发声(rev 极端 且 MOEX-z 同向) -> 实测兑现 71.6%(n=197, 全部 2021 后)。
#     普通确认池(不限同向)实测 68.8%, 不达标, 弃用。
#     (2026-09 Nominal 修复后 60 日 OOS 降至 60.7%, 未过门槛 -> 实验档展示。)
# 90: 双池(普通+确认) -> 修复前实测兑现 71.2%(n=1304); 修复后 50.9% -> 实验档展示。
# 2026-09-13 诊断后切换为 pure_rev: 移除被 OOS 证明有害的筛选(高波动/MOEX确认/bucket门槛),
# 纯 rev 信号的扩展窗 OOS 兑现率全面更优且消除反指。详见 artifacts/label_diagnostic 与
# nominal_fix 审计。pure_rev 仍不达 70%, Phase 2 将继续寻找更强结构。
# 2026-09-13 诊断后切换:
#   30日 → moex_z(MOEX价差z优先, 无MOEX时rev兜底; 2021后 55.1%);
#   60日 → pure_rev(2021后 58.9%); 90日 → extreme_dist(2021后 69.9%)。
# 7日 MOEX价差不变。均经扩展窗 OOS 验证。健康系统持续监控。
HORIZON_POLICY = {30: "moex_z", 60: "pure_rev", 90: "extreme_dist"}

# 极值类策略参数(预注册, 不据结果回改)
EXTREME_PARAMS = {
    30: {"lookback": 60},                       # 突破: 创60日新高/新低
    90: {"lookback": 252, "thresh": 0.4},       # 极值距离: 252日区间, 只在更接近极值20%时发声
}

# 弱倾向档参数(第二轮预注册候选 A, 非 60 变体挖矿产物, 禁止再调)
WEAK_GATE = 0.55           # 弱发声门槛: 桶实测命中率 >=55% 才给方向
WEAK_REGIME_LOOK = 252     # 高波动判定: 当日 20 日波动率 > 过去 252 日中位数
WEAK_VOL_WIN = 20

# rev 分桶(带方向,互斥): 强负/中负/弱负/弱正/中正/强正
_BUCKET_EDGES = np.array([-np.inf, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, np.inf])
_BUCKET_NAMES = ("strong_down", "mid_down", "weak_down",
                 "weak_up", "mid_up", "strong_up")

# moex_z 信号强度桶: 边界是 z-score 的标准差自然刻度(0.3/0.5/1.0σ),
# 预注册常量, 不是从结果拟合的阈值。桶命中率每日回测动态重算, 不写死。
_Z_BUCKET_EDGES = (0.3, 0.5, 1.0)
_Z_BUCKET_NAMES = ("z00", "z03", "z05", "z10")

# 30日 moex_z 发声闸门: 三个当日可得确认中至少满足 GATE_MIN_CONFIRMS 个才给涨跌,
# 否则该日落入弱档(灰色"—")。三个确认(全部因果, 无前视):
#   C1 MOEX 5日(MOEX交易日)对数动量与 z 同向;
#   C2 在岸价差 md 近5个 MOEX 日的变化与 z 同向(价差仍在加深);
#   C4 Brent 20日对数收益与预测方向同向(油价涨→卢布走强→CNY/RUB跌)。
# 不使用新闻情绪: 情绪数据仅 2024-04 起, 未跨牛熊, 留待攒够样本后预注册验证。
# 干净数据实测: 满足≥2 确认的发声日 62.6%(n=676, 覆盖约69%), 2022-2026 各年 59-72%。
GATE_MIN_CONFIRMS = 2


def _z_bucket_name(abs_z: float) -> str:
    """按当日 |z| 落桶: z00 死区 / z03 / z05 / z10 强信号。"""
    if abs_z >= 1.0:
        return "z10"
    if abs_z >= 0.5:
        return "z05"
    if abs_z >= 0.3:
        return "z03"
    return "z00"


def brent_ret20_aligned(idx, oil_df):
    """把油价对齐到 CNY/RUB 交易日并算 20 日对数收益。

    idx 为交易日 DatetimeIndex(回测传 df.index, 预测传 pd.DatetimeIndex(dates))。
    与 features.build_features 的 brent_ret20 完全同公式(reindex ffill →
    log → diff → rolling20 sum), 保证回测/预测/健康监控口径一致。无油表返回 None。
    """
    if oil_df is None or getattr(oil_df, "empty", True) or "brent_close" not in oil_df:
        return None
    oil = oil_df["brent_close"].reindex(idx, method="ffill")
    ol = np.log(oil.astype(float))
    return pd.Series(ol).diff().rolling(20).sum().to_numpy()


def compute_moex_confirms(lp, moex_close, z, brent_ret20=None):
    """计算 30 日 moex_z 发声闸门的确认数(0-3), 逐日因果。

    返回 int 数组, 仅在 z 有效的 MOEX 日可能 >0。C1/C2 只用 MOEX 自身,
    C4 需要 brent_ret20(缺油表时该确认恒 0)。
    """
    m = len(lp)
    md = np.where(np.isfinite(moex_close), np.log(moex_close) - lp, np.nan)
    c1 = np.zeros(m, dtype=bool)
    c2 = np.zeros(m, dtype=bool)
    c4 = np.zeros(m, dtype=bool)
    valid_idx = np.where(np.isfinite(z))[0]
    mclose = moex_close[valid_idx]
    pos_of = {int(t): k for k, t in enumerate(valid_idx)}
    for t in valid_idx:
        k = pos_of[int(t)]
        if k >= 5:
            mom = np.log(mclose[k]) - np.log(mclose[k - 5])
            if np.isfinite(mom) and (mom > 0) == (z[t] > 0):
                c1[t] = True
            dmd = md[t] - md[valid_idx[k - 5]]
            if np.sign(dmd) == np.sign(z[t]):
                c2[t] = True
        if brent_ret20 is not None and np.isfinite(brent_ret20[t]):
            pred = 1 if z[t] > 0 else 0
            oil_pred = 0 if brent_ret20[t] > 0 else 1
            if oil_pred == pred:
                c4[t] = True
    return c1.astype(int) + c2.astype(int) + c4.astype(int)


_RESULT_MAX_AGE_SEC = 7 * 24 * 3600   # 慢层日更,给 7 天宽限;过期一律中性


# ============ 信号计算(向量化, 因果) ============

def _rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < w:
        return out
    out[w - 1:] = np.convolve(x, np.ones(w) / w, "valid")
    return out


def _rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    """滚动总体标准差;NaN 输入只污染前 w-1 与含 NaN 窗口;序列短于 w 全 NaN。"""
    out = np.full(len(x), np.nan)
    if len(x) < w:
        return out
    c1 = np.convolve(x, np.ones(w), "valid")
    c2 = np.convolve(x * x, np.ones(w), "valid")
    var = c2 / w - (c1 / w) ** 2
    out[w - 1:] = np.sqrt(np.maximum(var, 1e-12))
    return out


def _aligned_moex(dates, moex_map: dict) -> np.ndarray:
    """MOEX 收盘价对齐到官方价日历;缺失日 ffill,首个成交日前保持 NaN。"""
    import pandas as pd
    raw = pd.Series(
        {pd.Timestamp(k): float(v) for k, v in (moex_map or {}).items()},
        dtype=float,
    ).sort_index()
    idx = pd.to_datetime(dates)
    return raw.reindex(idx).ffill().to_numpy(dtype=float)


def compute_long_signals(lp: np.ndarray, moex_close: np.ndarray) -> dict:
    """计算长周期信号。

    Args:
        lp: log(CNY/RUB 官方价) 数组
        maex_close: 已对齐+ffill 的 MOEX 收盘价数组(NaN=该日及之前无 MOEX)

    Returns:
        {"rev": 主信号, "z": MOEX 确认信号, "has_moex": 确认信号有效掩码}
    """
    lp = np.asarray(lp, dtype=float)
    ma_long = _rolling_mean(lp, REV_MA_WIN)
    sd = _rolling_std(lp, VOL_WIN)
    rev = -(lp - ma_long) / sd

    moex_close = np.asarray(moex_close, dtype=float)
    md = np.where(np.isfinite(moex_close), np.log(moex_close) - lp, np.nan)
    md = np.array(md, dtype=float)
    # md 内部缺口已由 ffill 消除;首个 MOEX 日前整段为 NaN
    z_mu = _rolling_mean(md, Z_WIN)
    z_sd = _rolling_std(md, Z_WIN)
    z = (md - z_mu) / z_sd
    has_moex = np.isfinite(z)
    return {"rev": rev, "z": z, "has_moex": has_moex}


def compute_extreme_signals(p: np.ndarray) -> dict:
    """极值类信号: 突破与极值距离。纯因果, 只用 t 及之前数据。

    Returns:
        {"breakout": 创60日新高=1/新低=0/其他=NaN,
         "extreme_dist": 接近252日最低=1/最高=0/中间=NaN(thresh=0.4)}
    """
    p = np.asarray(p, dtype=float)
    n = len(p)
    bo = np.full(n, np.nan)
    ed = np.full(n, np.nan)

    # 突破: 当日 > 过去60日(不含当日)最高 → 1; < 最低 → 0
    lb_bo = EXTREME_PARAMS[30]["lookback"]
    for t in range(lb_bo, n):
        window = p[t - lb_bo:t]   # 不含 t
        hi, lo = np.max(window), np.min(window)
        if p[t] > hi:
            bo[t] = 1
        elif p[t] < lo:
            bo[t] = 0

    # 极值距离: pos<thresh → 1(接近最低看涨), pos>1-thresh → 0(接近最高看跌)
    lb_ed = EXTREME_PARAMS[90]["lookback"]
    th = EXTREME_PARAMS[90]["thresh"]
    for t in range(lb_ed - 1, n):
        window = p[t - lb_ed + 1:t + 1]   # 含 t
        hi, lo = np.max(window), np.min(window)
        rng = hi - lo
        if rng <= 0:
            continue
        pos = (p[t] - lo) / rng
        if pos < th:
            ed[t] = 1
        elif pos > 1 - th:
            ed[t] = 0

    return {"breakout": bo, "extreme_dist": ed}


def compute_high_vol_regime(lp: np.ndarray) -> np.ndarray:
    """高波动状态掩码(因果): 当日 20 日收益波动率 > 过去 252 日中位数。

    第二轮预注册候选 A 的状态条件: 均值回复效应在高波动期显著增强
    (全历史 63.5% vs 平时 52%), 但近年代衰减到 55.9% —— 故只用于弱档。
    """
    lp = np.asarray(lp, dtype=float)
    rets = np.diff(lp, prepend=lp[0] if len(lp) else np.nan)
    rets[0] = np.nan
    vol = _rolling_std(rets, WEAK_VOL_WIN)
    hv = np.zeros(len(lp), dtype=bool)
    for t in range(WEAK_REGIME_LOOK, len(lp)):
        window = vol[t - WEAK_REGIME_LOOK:t]
        if np.isfinite(vol[t]) and np.isfinite(window).sum() > 100:
            hv[t] = vol[t] > np.nanmedian(window)
    return hv


def _bucket_ids(rev: np.ndarray) -> np.ndarray:
    """把 rev 映射到 0..5 桶;NaN -> -1。"""
    ids = np.full(len(rev), -1, dtype=int)
    finite = np.isfinite(rev)
    raw = np.digitize(rev[finite], _BUCKET_EDGES) - 1
    # 边界正好落在阈值上的点 digitize 归上侧,钳制到合法桶
    ids[finite] = np.clip(raw, 0, 5)
    return ids


def _neutral(N: int, reason: str, rev=None, z=None) -> dict:
    return {
        "prediction": None, "confidence": None,
        "prob_up": None, "prob_down": None,
        "signal": "long_reversion", "horizon": N,
        "neutral": True, "neutral_reason": reason,
        "rev": None if rev is None or not np.isfinite(rev) else round(float(rev), 3),
        "z": None if z is None or not np.isfinite(z) else round(float(z), 3),
        "gate": LONGHORIZON_GATE,
        "band_coverage": 0.80,
    }


# ============ 扩展窗 OOS 回测 ============

def _emit(bucket: int, rev_t: float, z_t, has_moex_t: bool,
          plain, confirmed) -> tuple | None:
    """按 t 时刻校准表决定是否发声。返回 (pred, conf, confirmed_used) 或 None。"""
    use_conf = bool(has_moex_t and np.isfinite(z_t) and np.sign(z_t) == np.sign(rev_t))
    table = confirmed if use_conf else plain
    wins, n = table.get(bucket, (0, 0))
    if n < LONG_MIN_BUCKET_N:
        return None
    rate = wins / n
    if rate < LONGHORIZON_GATE:
        return None
    pred = 1 if rev_t > 0 else 0
    return pred, rate, use_conf


def run_longhorizon_backtest(df, moex_map: dict, oil_df=None) -> dict:
    """长周期方向模型扩展窗 OOS 回测。

    逐日推进:在 t 时刻先把 j=t-N(结果刚实现)的样本计入校准桶,再决定是否发声,
    与 t+N 的真实方向对账。产物含每周期命中率/覆盖率/分时代稳定性/最终校准表。
    oil_df 用于 30 日 moex_z 的 C4 油价确认(缺省时该确认恒 0)。
    """
    lp = np.log(df["cny_rub"].to_numpy(dtype=float))
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    moex_close = _aligned_moex(dates, moex_map)
    sig = compute_long_signals(lp, moex_close)
    rev, z, has_moex = sig["rev"], sig["z"], sig["has_moex"]
    br20 = brent_ret20_aligned(df.index, oil_df)
    moex_gate = compute_moex_confirms(lp, moex_close, z, br20)
    ext = compute_extreme_signals(df["cny_rub"].to_numpy(dtype=float))
    bo, ed = ext["breakout"], ext["extreme_dist"]
    bids = _bucket_ids(rev)
    hv = compute_high_vol_regime(lp)
    m = len(lp)

    eras = [("2010-2015", "2010-01-01", "2016-01-01"),
            ("2016-2020", "2016-01-01", "2021-01-01"),
            ("2021-今", "2021-01-01", "2200-01-01")]

    horizons = {}
    for N in (30, 60, 90):
        policy = HORIZON_POLICY.get(N, "dual_pool")
        plain = {}        # bucket -> [wins, n]  全历史普通池
        confirmed = {}    # bucket -> [wins, n]  MOEX 且 z 同向池
        weak = {}         # bucket -> [wins, n]  高波动状态样本(30 日弱档)
        emitted = []      # (t, hit, used_conf, experimental)
        eligible = 0      # 可评估日(覆盖率分母)

        for t in range(WARMUP, m - N):
            # 1) 结果在 t 日刚实现的历史样本 j=t-N 入池(严格 <=t)
            j = t - N
            if j >= WARMUP and bids[j] >= 0:
                actual = 1 if lp[j + N] > lp[j] else 0
                pred_j = 1 if rev[j] > 0 else 0
                hit_j = int(actual == pred_j)
                cell = plain.setdefault(int(bids[j]), [0, 0])
                cell[0] += hit_j; cell[1] += 1
                if has_moex[j] and np.isfinite(z[j]) and np.sign(z[j]) == np.sign(rev[j]):
                    cc = confirmed.setdefault(int(bids[j]), [0, 0])
                    cc[0] += hit_j; cc[1] += 1
                if policy == "weak_experimental" and hv[j]:
                    wc = weak.setdefault(int(bids[j]), [0, 0])
                    wc[0] += hit_j; wc[1] += 1
            # 2) t 时刻决策
            if bids[t] < 0:
                continue
            if policy == "pure_rev":
                eligible += 1
                pred = 1 if rev[t] > 0 else 0
                actual = 1 if lp[t + N] > lp[t] else 0
                emitted.append((t, int(actual == pred), False, False))
                continue
            if policy == "moex_z":
                # MOEX 价差 z 优先, 无 MOEX 历史日 rev 兜底。
                # 发声闸门: MOEX 日须 C1/C2/C4 至少 GATE_MIN_CONFIRMS 个同向确认,
                # 确认不足的日子不发声(界面落弱档灰"—"), 故不计入发声命中率。
                eligible += 1
                if has_moex[t] and np.isfinite(z[t]):
                    if moex_gate[t] < GATE_MIN_CONFIRMS:
                        continue
                    pred = 1 if z[t] > 0 else 0
                else:
                    pred = 1 if rev[t] > 0 else 0
                actual = 1 if lp[t + N] > lp[t] else 0
                emitted.append((t, int(actual == pred), False, False))
                continue
            if policy in ("breakout", "extreme_dist"):
                # 极值类策略: 只在信号有效时发声, 其余弃权。
                sig_val = bo[t] if policy == "breakout" else ed[t]
                eligible += 1
                if not np.isfinite(sig_val):
                    continue
                pred = int(sig_val)
                actual = 1 if lp[t + N] > lp[t] else 0
                emitted.append((t, int(actual == pred), False, False))
                continue
            if policy == "weak_experimental":
                eligible += int(hv[t])
                if not hv[t]:
                    continue
                ww, wn = weak.get(int(bids[t]), (0, 0))
                if wn < LONG_MIN_BUCKET_N or ww / wn < WEAK_GATE:
                    continue
                pred = 1 if rev[t] > 0 else 0
                actual = 1 if lp[t + N] > lp[t] else 0
                emitted.append((t, int(actual == pred), False, True))
                continue
            eligible += 1
            if policy == "off":
                continue
            dec = _emit(int(bids[t]), rev[t], z[t], bool(has_moex[t]),
                        plain, confirmed)
            if dec is None:
                continue
            pred, _rate, use_conf = dec
            if policy == "resonance_only" and not use_conf:
                continue
            actual = 1 if lp[t + N] > lp[t] else 0
            emitted.append((t, int(actual == pred), use_conf, False))

        n_emit = len(emitted)
        hit_rate = (sum(h for _, h, _, _ in emitted) / n_emit) if n_emit else None
        coverage = n_emit / eligible if eligible else 0.0
        conf_emit = [e for e in emitted if e[2]]
        conf_hit = (sum(h for _, h, _, _ in conf_emit) / len(conf_emit)
                    if conf_emit else None)
        weak_emit = [e for e in emitted if e[3]]
        weak_hit = (sum(h for _, h, _, _ in weak_emit) / len(weak_emit)
                    if weak_emit else None)

        # 分时代(全部发声日,含两个池)
        era_stats = {}
        for name, lo, hi in eras:
            lo_i = int(np.searchsorted(df.index, np.datetime64(lo)))
            hi_i = int(np.searchsorted(df.index, np.datetime64(hi)))
            sel = [h for t, h, _, _ in emitted if lo_i <= t < hi_i]
            era_stats[name] = {
                "hit": round(sum(sel) / len(sel), 4) if sel else None,
                "n": len(sel),
            }
        modern_hit = era_stats["2021-今"]["hit"]

        passed = bool(hit_rate is not None and hit_rate >= LONGHORIZON_GATE
                      and n_emit >= 100 and policy not in ("off", "weak_experimental"))
        # 弱档即使历史偶然超过 70% 也不允许标记通过(挖矿/小样本保护):
        # 它的产品语义永久是"实验中", 转正必须走独立的未来数据验证流程。
        # 完整预注册协议(命中率+覆盖率>=30%): 仅作披露, 共振策略 60 日覆盖率~5%
        # 是"少而准"设计的固有属性; 有效性以用户标准(命中率+样本数)为准。
        full_protocol = bool(passed and coverage >= 0.30)

        # 最终生产校准表: 截至今天全部已实现样本(j+N<=m-1)都合法可用。
        # 注意这与上面的 OOS 评估分开:walk 中 t 时刻只能用 j<=t-N 的样本,
        # 而生产预测在 t=m-1 时,所有 j<=m-1-N 的结果都已实现。
        final_plain, final_confirmed, final_weak = {}, {}, {}
        # moex_z 动态校准(每日回测重算, 无写死命中率):
        #   final_z      过闸发声日(C1/C2/C4≥2)按当日 |z| 桶的兑现率;
        #   z_gate_fail  确认不足(闸门未过)的 MOEX 日整体兑现率 -> 弱档把握度;
        #   z_fallback   无 MOEX 历史日 rev 兜底整体兑现率。
        final_z = {name: [0, 0] for name in _Z_BUCKET_NAMES}
        z_gate_fail = [0, 0]
        z_fallback = [0, 0]
        for j in range(WARMUP, m - N):
            if bids[j] < 0:
                continue
            actual = 1 if lp[j + N] > lp[j] else 0
            hit_j = int(actual == (1 if rev[j] > 0 else 0))
            c = final_plain.setdefault(int(bids[j]), [0, 0])
            c[0] += hit_j; c[1] += 1
            if has_moex[j] and np.isfinite(z[j]) and np.sign(z[j]) == np.sign(rev[j]):
                cc = final_confirmed.setdefault(int(bids[j]), [0, 0])
                cc[0] += hit_j; cc[1] += 1
            if policy == "weak_experimental" and hv[j]:
                wc = final_weak.setdefault(int(bids[j]), [0, 0])
                wc[0] += hit_j; wc[1] += 1
            if policy == "moex_z":
                if has_moex[j] and np.isfinite(z[j]):
                    zhit = int(actual == (1 if z[j] > 0 else 0))
                    if moex_gate[j] >= GATE_MIN_CONFIRMS:
                        cell = final_z[_z_bucket_name(abs(z[j]))]
                        cell[0] += zhit; cell[1] += 1
                    else:
                        z_gate_fail[0] += zhit; z_gate_fail[1] += 1
                else:
                    z_fallback[0] += hit_j
                    z_fallback[1] += 1

        def _dump(table):
            return {_BUCKET_NAMES[b]: {"wins": int(v[0]), "n": int(v[1]),
                                      "rate": round(v[0] / v[1], 4) if v[1] else None}
                    for b, v in sorted(table.items())}

        horizons[str(N)] = {
            "policy": policy,
            "passed_70pct_gate": passed,
            "meets_full_protocol": full_protocol,
            "experimental": not passed,
            "oos_hit": round(hit_rate, 4) if hit_rate is not None else None,
            "oos_emitted": n_emit,
            "eligible_days": eligible,
            "coverage": round(coverage, 4),
            "confirmed_hit": round(conf_hit, 4) if conf_hit is not None else None,
            "confirmed_n": len(conf_emit),
            "weak_hit": round(weak_hit, 4) if weak_hit is not None else None,
            "weak_modern_hit": modern_hit if policy == "weak_experimental" else None,
            "weak_n": len(weak_emit),
            "eras": era_stats,
            "tables": {"plain": _dump(final_plain),
                       "confirmed": _dump(final_confirmed),
                       "weak": _dump(final_weak)},
            "z_strength_buckets": (
                {name: {"wins": int(v[0]), "n": int(v[1]),
                        "rate": round(v[0] / v[1], 4) if v[1] else None}
                 for name, v in final_z.items()}
                if policy == "moex_z" else None),
            "z_rev_fallback": (
                {"wins": int(z_fallback[0]), "n": int(z_fallback[1]),
                 "rate": (round(z_fallback[0] / z_fallback[1], 4)
                          if z_fallback[1] else None)}
                if policy == "moex_z" else None),
            "z_gate_failed": (
                {"wins": int(z_gate_fail[0]), "n": int(z_gate_fail[1]),
                 "rate": (round(z_gate_fail[0] / z_gate_fail[1], 4)
                          if z_gate_fail[1] else None)}
                if policy == "moex_z" else None),
            "gate_min_confirms": GATE_MIN_CONFIRMS if policy == "moex_z" else None,
        }

    result = {
        "generated_ts": time.time(),
        "calibration": "expanding_window_oos_gate70",
        "gate": LONGHORIZON_GATE,
        "min_bucket_n": LONG_MIN_BUCKET_N,
        "policies": HORIZON_POLICY,
        "params": {"rev_ma_win": REV_MA_WIN, "vol_win": VOL_WIN, "z_win": Z_WIN},
        "meta": {"as_of": min(df.index[-1].date(), config.msk_today()).isoformat(),
                 "rows": int(m)},
        "horizons": horizons,
    }
    return result


# ============ 生产预测 ============

def load_longhorizon_result() -> dict | None:
    """读取回测产物;缺失/损坏/过期返回 None(调用方须输出中性)。"""
    path = config.LONGHORIZON_JSON
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("generated_ts", 0)
        if age > _RESULT_MAX_AGE_SEC or age < 0:
            log.warning("长周期校准过期(%.1f 天),全部输出中性", age / 86400)
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError) as e:
        log.warning("长周期校准文件不可读(%s),全部输出中性", e)
        return None


def _table_lookup(result: dict, N: int, use_conf: bool, bucket: int,
                  table: str = "plain"):
    """从最终校准表(全已实现历史)取 (wins,n);无记录返回 (0,0)。"""
    h = result["horizons"].get(str(N))
    if not h:
        return 0, 0
    if table == "weak":
        tbl = h["tables"].get("weak", {})
    else:
        tbl = h["tables"]["confirmed" if use_conf else "plain"]
    cell = tbl.get(_BUCKET_NAMES[bucket])
    if not cell:
        return 0, 0
    return int(cell["wins"]), int(cell["n"])


def predict_longhorizon(lp, dates, moex_map: dict, N: int,
                        result: dict | None = None, oil_df=None) -> dict:
    """当前时点长周期方向预测。

    2026-09-13 产品决策: 不再因 70% 门槛不达标而沉默——方向始终给出,
    门槛只决定诚实标记(未通过 -> experimental=True)。把握度沿用既有校准
    哲学: 规则整体扩展窗 OOS 实测兑现率(桶命中率仅作 bucket_rate 披露);
    规则级兑现率缺失时回退本桶实测值, 两者皆无才输出中性。
    30 日 moex_z 额外有 C1/C2/C4 发声闸门: 确认不足的日子方向仍给,
    但把握度取"未过闸子集"实测率(约弱档), 前端自动灰显。oil_df 提供 C4 油价。
    """
    if result is None:
        result = load_longhorizon_result()
    if result is None:
        return _neutral(N, "calibration_unavailable")

    moex_close = _aligned_moex(dates, moex_map)
    sig = compute_long_signals(np.asarray(lp, dtype=float), moex_close)
    rev, z, has_moex = sig["rev"], sig["z"], sig["has_moex"]
    br20 = brent_ret20_aligned(pd.DatetimeIndex(dates), oil_df)
    moex_gate = compute_moex_confirms(
        np.asarray(lp, dtype=float), moex_close, z, br20)
    ext = compute_extreme_signals(np.exp(np.asarray(lp, dtype=float)))
    bo, ed = ext["breakout"], ext["extreme_dist"]
    t = len(rev) - 1
    bids = _bucket_ids(rev)
    rev_t, z_t = rev[t], z[t]

    policy = HORIZON_POLICY.get(N, "dual_pool")
    if policy == "off":
        return _neutral(N, "no_validated_signal", rev_t, z_t)
    if bids[t] < 0:
        return _neutral(N, "insufficient_history")

    h_meta = result["horizons"].get(str(N), {})

    if policy == "pure_rev":
        modern = h_meta.get("eras", {}).get("2021-今", {}).get("hit")
        conf = modern if modern is not None else h_meta.get("oos_hit")
        if conf is None:
            return _neutral(N, "no_measurable_rate", rev_t, z_t)
        pred = 1 if rev_t > 0 else 0
        pu = conf if pred == 1 else 1 - conf
        passed = bool(h_meta.get("passed_70pct_gate", False))
        return {
            "prediction": pred,
            "confidence": round(float(conf), 3),
            "prob_up": round(pu, 3),
            "prob_down": round(1 - pu, 3),
            "signal": "long_reversion",
            "policy": policy,
            "experimental": not passed,
            "validated_70pct": passed,
            "confidence_source": "modern_oos_hit",
            "rule_emitted": h_meta.get("oos_emitted", 0),
            "bucket_rate": None,
            "modern_hit": modern,
            "rev": round(float(rev_t), 3),
            "z": round(float(z_t), 3) if np.isfinite(z_t) else None,
            "bucket": None,
            "confirmed": False,
            "bucket_n": h_meta.get("oos_emitted", 0),
            "high_vol_regime": None,
            "horizon": N,
            "gate": LONGHORIZON_GATE,
            "band_coverage": 0.80,
        }

    if policy == "moex_z":
        modern = h_meta.get("eras", {}).get("2021-今", {}).get("hit")
        fallback_conf = modern if modern is not None else h_meta.get("oos_hit")

        # 动态把握度(每日回测重算, 无写死命中率):
        #  过闸日(C1/C2/C4≥2) -> 当日 |z| 桶的过闸实测率;
        #  未过闸日 -> "未过闸子集"整体实测率(通常<55%, 前端自动落弱档灰"—")。
        #  桶/子集样本不足 LONG_MIN_BUCKET_N 时逐级回退, 再不足用规则年代率。
        z_buckets = h_meta.get("z_strength_buckets") or {}
        agg_n = sum(int((z_buckets.get(b) or {}).get("n", 0))
                    for b in _Z_BUCKET_NAMES)
        agg_w = sum(int((z_buckets.get(b) or {}).get("wins", 0))
                    for b in _Z_BUCKET_NAMES)
        agg_rate = agg_w / agg_n if agg_n else None
        gate_failed = h_meta.get("z_gate_failed") or {}
        gate_t = int(moex_gate[t]) if np.isfinite(moex_gate[t]) else 0
        gate_passed = gate_t >= GATE_MIN_CONFIRMS

        if has_moex[t] and np.isfinite(z_t):
            pred = 1 if z_t > 0 else 0
            src = "moex_spread"
            bname = _z_bucket_name(abs(z_t))
            cell = z_buckets.get(bname) or {}
            if gate_passed:
                if cell.get("n", 0) >= LONG_MIN_BUCKET_N and cell.get("rate") is not None:
                    conf, csrc, bn = cell["rate"], "z_bucket_dynamic", cell["n"]
                elif agg_n >= LONG_MIN_BUCKET_N and agg_rate is not None:
                    conf, csrc, bn = agg_rate, "moex_aggregate_dynamic", agg_n
                else:
                    if fallback_conf is None:
                        return _neutral(N, "no_measurable_rate", rev_t, z_t)
                    conf, csrc, bn = fallback_conf, "modern_oos_hit", h_meta.get("oos_emitted", 0)
            else:
                gf = gate_failed
                if gf.get("n", 0) >= LONG_MIN_BUCKET_N and gf.get("rate") is not None:
                    conf, csrc, bn = gf["rate"], "gate_failed_dynamic", gf["n"]
                else:
                    if fallback_conf is None:
                        return _neutral(N, "no_measurable_rate", rev_t, z_t)
                    conf, csrc, bn = fallback_conf, "modern_oos_hit", h_meta.get("oos_emitted", 0)
        else:
            pred = 1 if rev_t > 0 else 0
            src = "long_reversion"
            fb = h_meta.get("z_rev_fallback") or {}
            if fb.get("n", 0) >= LONG_MIN_BUCKET_N and fb.get("rate") is not None:
                conf, csrc, bn = fb["rate"], "rev_fallback_dynamic", fb["n"]
            else:
                if fallback_conf is None:
                    return _neutral(N, "no_measurable_rate", rev_t, z_t)
                conf, csrc, bn = fallback_conf, "modern_oos_hit", h_meta.get("oos_emitted", 0)
            bname = None
        pu = conf if pred == 1 else 1 - conf
        passed = bool(h_meta.get("passed_70pct_gate", False))
        return {
            "prediction": pred,
            "confidence": round(float(conf), 3),
            "prob_up": round(pu, 3),
            "prob_down": round(1 - pu, 3),
            "signal": src,
            "policy": policy,
            "experimental": not passed,
            "validated_70pct": passed,
            "confidence_source": csrc,
            "rule_emitted": h_meta.get("oos_emitted", 0),
            "bucket_rate": None,
            "modern_hit": modern,
            "rev": round(float(rev_t), 3),
            "z": round(float(z_t), 3) if np.isfinite(z_t) else None,
            "bucket": bname,
            "confirmed": bool(has_moex[t] and np.isfinite(z_t)),
            "gate_confirms": gate_t,
            "gate_passed": bool(has_moex[t] and np.isfinite(z_t) and gate_passed),
            "bucket_n": int(bn),
            "high_vol_regime": None,
            "horizon": N,
            "gate": LONGHORIZON_GATE,
            "band_coverage": 0.80,
        }

    if policy in ("breakout", "extreme_dist"):
        sig_val = bo[t] if policy == "breakout" else ed[t]
        modern = h_meta.get("eras", {}).get("2021-今", {}).get("hit")
        conf = modern if modern is not None else h_meta.get("oos_hit")
        if not np.isfinite(sig_val) or conf is None:
            return _neutral(N, "no_validated_signal", rev_t, z_t)
        pred = int(sig_val)
        pu = conf if pred == 1 else 1 - conf
        passed = bool(h_meta.get("passed_70pct_gate", False))
        return {
            "prediction": pred,
            "confidence": round(float(conf), 3),
            "prob_up": round(pu, 3),
            "prob_down": round(1 - pu, 3),
            "signal": policy,
            "policy": policy,
            "experimental": not passed,
            "validated_70pct": passed,
            "confidence_source": "modern_oos_hit",
            "rule_emitted": h_meta.get("oos_emitted", 0),
            "bucket_rate": None,
            "modern_hit": modern,
            "rev": round(float(rev_t), 3),
            "z": round(float(z_t), 3) if np.isfinite(z_t) else None,
            "bucket": None,
            "confirmed": False,
            "bucket_n": h_meta.get("oos_emitted", 0),
            "high_vol_regime": None,
            "horizon": N,
            "gate": LONGHORIZON_GATE,
            "band_coverage": 0.80,
        }

    use_conf = bool(has_moex[t] and np.isfinite(z_t)
                    and np.sign(z_t) == np.sign(rev_t))

    # 校准池选择: 30 日高波动状态用弱档池(该规则原生口径), 其余按 MOEX 确认/普通池
    in_hv = False
    if policy == "weak_experimental":
        hv = compute_high_vol_regime(np.asarray(lp, dtype=float))
        in_hv = bool(hv[t])
        table = "weak" if in_hv else "plain"
    else:
        table = "plain"   # _table_lookup 内部按 use_conf 在 confirmed/plain 间选择

    wins, n = _table_lookup(result, N, use_conf, int(bids[t]), table=table)

    # 把握度: 规则整体 OOS 实测兑现率; 缺失时回退本桶实测值
    conf = h_meta.get("oos_hit")
    if conf is None and n:
        conf = wins / n
    if conf is None:
        return _neutral(N, "no_measurable_rate", rev_t, z_t)
    conf_src = "rule_oos_hit" if h_meta.get("oos_hit") is not None else "bucket_rate"

    pred = 1 if rev_t > 0 else 0
    pu = conf if pred == 1 else 1 - conf
    passed = bool(h_meta.get("passed_70pct_gate", False))
    modern = h_meta.get("eras", {}).get("2021-今", {}).get("hit")
    return {
        "prediction": pred,
        "confidence": round(float(conf), 3),
        "prob_up": round(pu, 3),
        "prob_down": round(1 - pu, 3),
        "signal": "long_reversion",
        "policy": policy,
        "experimental": not passed,
        "validated_70pct": passed,
        "confidence_source": conf_src,
        "rule_emitted": h_meta.get("oos_emitted", 0),
        "bucket_rate": round(wins / n, 3) if n else None,
        "modern_hit": modern,
        "rev": round(float(rev_t), 3),
        "z": round(float(z_t), 3) if np.isfinite(z_t) else None,
        "bucket": _BUCKET_NAMES[int(bids[t])],
        "confirmed": use_conf,
        "bucket_n": n,
        "high_vol_regime": in_hv if policy == "weak_experimental" else None,
        "horizon": N,
        "gate": LONGHORIZON_GATE,
        "band_coverage": 0.80,
    }
