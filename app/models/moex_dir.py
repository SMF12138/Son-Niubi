"""MOEX 增强方向预测器(MoexDirectionPredictor)。

融合两条无前视信号:
1. **MOEX 在岸偏离**(主, 短窗强): dev = log(MOEX市场价) - log(CBR官方价)。
   市场价领先, 官方价上追。滚动标准化 zdev>0 → 看涨。
   实测 7 日 65.9%(高置信 74.6%, 近500天 67.6%)。
2. **线性合成均值回复**(备, 长窗强): 无 MOEX 数据的历史日期退回它。

MOEX 数据 2022-06 起; 更早日期只有均值回复。因此:
- 有 MOEX 偏离 → 以 zdev 为主, 高置信
- 无 MOEX → 退回 MeanRevDirectionPredictor

校准值(_CAL/_CAP):
- 硬编码默认值作为 fallback
- 优先从 data/calibration.json 加载动态校准值(每日自动更新)
- JSON 48 小时内有效, 过期则回退默认值

接口与 direction_plus.EnhancedDirectionPredictor 一致。
"""
import json
import logging
import time

import numpy as np

from app import config
from app.models.meanrev_dir import MeanRevDirectionPredictor

log = logging.getLogger(__name__)

# === 硬编码默认校准值(fallback) ===
# z15 > z10 > z05 > z00 单调递减(偏离越大置信越高)
_DEFAULT_CAL = {
    7:  {"z15": 0.79, "z10": 0.72, "z05": 0.65, "z00": 0.62},
    30: {"z15": 0.75, "z10": 0.65, "z05": 0.60, "z00": 0.56},
    60: {"z15": 0.72, "z10": 0.65, "z05": 0.60, "z00": 0.54},
    90: {"z15": 0.71, "z10": 0.62, "z05": 0.57, "z00": 0.50},
}
_DEFAULT_CAP = {
    7:  {"z15": 0.79, "z10": 0.72, "z05": 0.65, "z00": 0.62},
    30: {"z15": 0.75, "z10": 0.65, "z05": 0.60, "z00": 0.56},
    60: {"z15": 0.72, "z10": 0.65, "z05": 0.60, "z00": 0.54},
    90: {"z15": 0.71, "z10": 0.62, "z05": 0.57, "z00": 0.50},
}

_CALIBRATION_PATH = config.DATA_DIR / "calibration.json"
_CAL_MAX_AGE_SEC = 48 * 3600  # 48小时有效

# |z| 累积分桶(与 calibrate_moex_z 口径一致): 命中第一个满足的阈值即归桶,
# 所以 z10 桶包含 z>1.5 的样本 —— P(命中 | |z|>阈值), 非互斥分箱。
_Z_BUCKETS = (("z15", 1.5), ("z10", 1.0), ("z05", 0.5), ("z00", 0.0))
# OOS 校准中每桶至少要有这么多已实现样本才采用实测命中率, 否则回退内置默认表
_OOS_MIN_BUCKET_N = 20
# |z| 低于此值视为"无方向性信号": 预测符号仍给出, 但界面须标注为中性/基础胜率
_WEAK_Z = 0.3


def _bucket_key(az: float) -> str:
    for bk, thr in _Z_BUCKETS:
        if az > thr:
            return bk
    return "z00"


def _oos_bucket_table(N: int, hits: dict, totals: dict) -> dict:
    """扩展窗样本外校准表: 每桶实测命中率; 样本不足的桶回退内置默认值。

    回测中 t 时刻的置信度只能由"结果已实现"的历史窗口(j+N<=t)统计得到。
    """
    default = _DEFAULT_CAL.get(N, _DEFAULT_CAL[7])
    return {bk: (round(hits[bk] / totals[bk], 4) if totals[bk] >= _OOS_MIN_BUCKET_N
                 else default[bk])
            for bk, _ in _Z_BUCKETS}


def _load_calibration():
    """从 JSON 加载动态校准值, 过期或缺失则返回 None。"""
    if not _CALIBRATION_PATH.exists():
        return None, None, None
    try:
        with open(_CALIBRATION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        generated = data.get("generated_ts", 0)
        age = time.time() - generated
        if age > _CAL_MAX_AGE_SEC or age < 0:   # age<0: 时间戳来自未来, 一律视为无效
            log.warning("校准文件过期(%.1f小时前), 用默认值 —— 把握度已回退到内置表", age / 3600)
            return None, None, None
        cal = {int(k): v for k, v in data["cal"].items()}
        raw_cap = data.get("cap", {})
        # 兼容新格式(dict per-horizon)和旧格式(scalar per-horizon)
        cap = {}
        for k, v in raw_cap.items():
            if isinstance(v, dict):
                cap[int(k)] = v
            else:
                cap[int(k)] = _DEFAULT_CAP.get(int(k), _DEFAULT_CAL.get(int(k), {}))
        meanrev = {int(k): v for k, v in data.get("meanrev", {}).items()}
        log.info("加载动态校准值(%.1f小时前生成)", age / 3600)
        return cal, cap, meanrev
    except Exception as e:
        log.warning("加载校准文件失败: %s, 用默认值", e)
        return None, None, None


def calibration_status() -> dict:
    """当前校准文件状态, 供 API/界面显示 —— 回退到内置默认表时必须让用户看得见。

    返回 {"age_h": 小时数或 None(文件缺失/损坏), "is_fallback": 是否正在用默认值}。
    """
    if not _CALIBRATION_PATH.exists():
        return {"age_h": None, "is_fallback": True}
    try:
        with open(_CALIBRATION_PATH, "r", encoding="utf-8") as f:
            generated = json.load(f).get("generated_ts", 0)
    except Exception:
        return {"age_h": None, "is_fallback": True}
    age = time.time() - generated
    return {"age_h": round(age / 3600, 1),
            "is_fallback": age > _CAL_MAX_AGE_SEC or age < 0}


class MoexDirectionPredictor:
    name = "MOEX-DIR"

    def __init__(self):
        self._fallback = MeanRevDirectionPredictor()
        # attach_moex 后填充的对齐数组(按 MOEX 有成交的日期顺序):
        self._h_idx = None        # np.ndarray: 官方价行号(升序)
        self._h_pos = None        # {row_index: 在对齐数组中的位置}
        self._h_dev = None        # np.ndarray: raw_dev
        self._h_close = None      # np.ndarray: MOEX 收盘
        self._h_hlr = None        # np.ndarray: (high-low)/close, 缺失为 NaN
        self._h_hlr_med = None    # np.ndarray: 截至当日的日内幅历史中位(无前视, <30 为 NaN)
        self._h_z = None          # np.ndarray: 滚动 z(win150), <60 为 NaN
        # 动态校准: 优先 JSON, 回退默认值
        cal, cap, meanrev = _load_calibration()
        self._cal = cal or _DEFAULT_CAL
        self._cap = cap or _DEFAULT_CAP
        self._meanrev_conf = meanrev or {}
        # 将 meanrev 校准传给 fallback
        if self._meanrev_conf:
            self._fallback.set_calibration(self._meanrev_conf)

    def attach_moex(self, dates, off_lp, moex_map, hl_map=None):
        """预计算每个官方交易日的 raw_dev + MOEX 收盘/日内幅(用于多重确认)。
        dates: 官方价日期字符串列表(与 lp 对齐); off_lp: log(官方价);
        moex_map: {date_str: moex_close}; hl_map: {date_str: (close,high,low)} 可选。

        所有派生量一次性预算成对齐数组 —— predict_direction 在回测中被调用
        数万次, 不得每次都对全历史做列表过滤(旧实现 O(n^2))。"""
        idxs, devs, closes, hlrs = [], [], [], []
        for i, d in enumerate(dates):
            mv = moex_map.get(d)
            if mv is not None and mv > 0:
                idxs.append(i)
                devs.append(float(np.log(mv) - off_lp[i]))
                closes.append(float(mv))
                v = np.nan
                if hl_map and d in hl_map:
                    c, h, lo = hl_map[d]
                    if c and h and lo:
                        v = (h - lo) / c
                hlrs.append(v)
        n = len(idxs)
        self._h_idx = np.asarray(idxs, dtype=int)
        self._h_pos = {j: t for t, j in enumerate(idxs)}
        self._h_dev = np.asarray(devs, dtype=float)
        self._h_close = np.asarray(closes, dtype=float)
        self._h_hlr = np.asarray(hlrs, dtype=float)
        # 滚动 z(win=150, 无前视), 至少 60 个 MOEX 日才计算
        z = np.full(n, np.nan)
        for t in range(n):
            if t + 1 >= 60:
                a = self._h_dev[max(0, t + 1 - config.ZDEV_WINDOW):t + 1]
                z[t] = (self._h_dev[t] - a.mean()) / (a.std() + 1e-9)
        self._h_z = z
        # 截至每个 MOEX 日的日内幅历史中位(只用 <=t 的非缺失值, 至少 30 个)
        med = np.full(n, np.nan)
        for t in range(n):
            hist = self._h_hlr[:t + 1]
            hist = hist[~np.isnan(hist)]
            if len(hist) >= 30:
                med[t] = np.median(hist)
        self._h_hlr_med = med

    def _resolve_dev_pos(self, i):
        """返回行 i 应使用的 MOEX 序列位置: 当日, 否则向前 ≤3 个交易日; 无则 None。"""
        if self._h_pos is None:
            return None
        p = self._h_pos.get(i)
        if p is not None:
            return p
        # 在对齐行号数组上二分定位 < i 的最近一个, 要求间隔 ≤3
        ins = int(np.searchsorted(self._h_idx, i, side="left"))
        if ins == 0:
            return None
        p = ins - 1
        return p if i - int(self._h_idx[p]) <= 3 else None

    def predict_direction(self, ctx, N, cal_tbl=None, cap_tbl=None,
                          meanrev_conf=None):
        """cal_tbl/cap_tbl: 回测时传入的 t 时刻扩展窗样本外校准表(4 桶);
        meanrev_conf: {N: hit_rate} 给 fallback 预测器的样本外命中率。
        不传则用实例的全样本动态校准(生产预测时点全部历史可得, 合法)。"""
        i = ctx["i"]

        def _fallback_predict():
            if meanrev_conf is None:
                return self._fallback.predict_direction(ctx, N)
            return self._fallback.predict_direction(ctx, N,
                                                    conf_override=meanrev_conf)

        # === 30天: 优先用均值回复 + USD/RUB 确认 ===
        if 30 <= N < 90:
            r = _fallback_predict()
            if r.get("signal") == "meanrev_strong":
                return r
            # 均值回复弱信号时,如果也有 MOEX 偏离,混合判断
            # (不直接用 MOEX 做主信号,只作参考)

        # === 7天 / 30天无强信号 / 90天: MOEX 偏离(核心信号) ===
        p = self._resolve_dev_pos(i)
        if p is not None and p + 1 >= 60:
            arr = self._h_dev[max(0, p + 1 - config.ZDEV_WINDOW):p + 1]
            mu = arr.mean(); sd = arr.std() + 1e-9
            z = (self._h_dev[p] - mu) / sd
            pred = 1 if z > 0 else 0
            az = abs(z)

            # === 多重确认信号(均当日可得, 无前视) ===
            confirms = 0
            # 确认1: MOEX 5日动量与 zdev 同向(取 [i-5, i] 内最前/最后 MOEX 收盘)
            win_mask = (self._h_idx >= i - 5) & (self._h_idx <= i)
            win_pos = np.where(win_mask)[0]
            if len(win_pos) >= 2:
                mom = (np.log(self._h_close[win_pos[-1]])
                       - np.log(self._h_close[win_pos[0]]))
                if (mom > 0) == (z > 0):
                    confirms += 1
            # 确认2: 偏离加深(dev 近6个 MOEX 日变化与 z 同号)
            if p + 1 >= 6:
                dz = self._h_dev[p] - self._h_dev[p - 5]
                if np.sign(dz) == np.sign(z):
                    confirms += 1
            # 确认3: 高波动日(日内幅 ≥ 历史中位)
            if p < len(self._h_hlr) and not np.isnan(self._h_hlr[p]) \
                    and not np.isnan(self._h_hlr_med[p]):
                if self._h_hlr[p] >= self._h_hlr_med[p]:
                    confirms += 1
            # 确认4: 油价20日动量与 zdev 同向(油价涨→卢布走强→CNY/RUB跌)
            feat_names = ctx.get("feat_names", [])
            Xf = ctx.get("Xf")
            if Xf is not None and "brent_ret20" in feat_names:
                br_idx = feat_names.index("brent_ret20")
                pos_of2 = np.searchsorted(ctx["valid"], i)
                if pos_of2 < len(ctx["valid"]) and ctx["valid"][pos_of2] == i:
                    br20 = Xf[i, br_idx]
                    if not np.isnan(br20):
                        # 油价涨→卢布走强→CNY/RUB跌(pred=0), 与 z 同向则确认
                        oil_pred = 0 if br20 > 0 else 1
                        if oil_pred == pred:
                            confirms += 1
            # 确认5: 近7日新闻情绪与方向一致
            if Xf is not None and "sentiment_7d" in feat_names:
                sent_idx = feat_names.index("sentiment_7d")
                pos_of3 = np.searchsorted(ctx["valid"], i)
                if pos_of3 < len(ctx["valid"]) and ctx["valid"][pos_of3] == i:
                    sent7 = Xf[i, sent_idx]
                    if not np.isnan(sent7) and abs(sent7) > 0.01:
                        # 负面情绪→CNY/RUB波动大→偏跌
                        sent_pred = 0 if sent7 < 0 else 1
                        if sent_pred == pred:
                            confirms += 1
            # 确认6: 连续偏离天数(z 方向一致 ≥ 2 日)
            streak = 0
            for q in range(p, max(p - 6, -1), -1):
                zj = self._h_z[q]
                if not np.isnan(zj) and np.sign(zj) == np.sign(z):
                    streak += 1
                else:
                    break
            if streak >= 2:
                confirms += 1

            # === 置信度: 按 horizon 分别校准(OOS 传入 or 全样本动态表) ===
            # cal_tbl 非空时就是本 horizon 的 4 桶表; 否则从实例的 {N: 表} 取最近 N
            if cal_tbl is not None:
                tbl = cal_tbl
                cap_lookup = cap_tbl if cap_tbl is not None else tbl
            else:
                cal_n = min(self._cal.keys(), key=lambda k: abs(k - N))
                tbl = self._cal[cal_n]
                cap_lookup = self._cap.get(cal_n, _DEFAULT_CAP.get(cal_n, {}))
            bucket_key = _bucket_key(az)
            conf = tbl[bucket_key]
            cap = cap_lookup.get(bucket_key, 0.7)
            # 六确认只作展示字段(confirms), 不参与把握度: 2026-09 条件命中率实测,
            # "确认≥2"相对"0-1"零增量(60.1% vs 59.7%), "≥3"在 z00 桶有效(63.4% vs
            # 50.9%)但在 z05 桶反向(69.3% vs 92.3%, 小样本), 增量不稳定且接线等于
            # 新一轮同数据挖规则, 故把握度严格等于该 |z| 桶的(动态)校准命中率。
            #
            # 桶内微调: 同一桶内 |z| 越大信号越强, 向更高桶命中率方向内插一小段
            # (系数 0.25), 让置信度随 |z| 轻微浮动而不是同桶恒等。锚点仍是本桶校准
            # 命中率, 偏离幅度小(±~1.5%), 不违反校准口径诚实性。
            _NEXT = {"z00": ("z05", 0.0, 0.5),
                     "z05": ("z10", 0.5, 1.0),
                     "z10": ("z15", 1.0, 1.5),
                     "z15": (None, 1.5, None)}
            nxt = _NEXT.get(bucket_key)
            if nxt and nxt[0] is not None and nxt[2] is not None:
                nxt_key, lo, hi = nxt
                pos = min(max((az - lo) / (hi - lo), 0.0), 1.0)
                nxt_conf = tbl.get(nxt_key, conf)
                conf = conf + (nxt_conf - conf) * pos * 0.25
            conf = max(0.5, min(conf, cap))
            pu = conf if pred == 1 else 1 - conf
            return {"prediction": pred, "confidence": round(conf, 3),
                    "prob_up": round(pu, 3), "prob_down": round(1 - pu, 3),
                    "signal": "moex_dev", "z": round(float(z), 3),
                    "z_bucket": bucket_key,
                    "weak_signal": bool(az < _WEAK_Z),
                    "confirms": int(confirms), "horizon": N}
        # 无 MOEX → 退回均值回复
        r = _fallback_predict()
        r["signal"] = "mean_rev"
        return r


def _build_moex_ctx(df):
    """构造带 MOEX 偏离的 predictor(预 attach)。返回 (predictor, lp, dates)。"""
    from app.data.moex_rates import load_moex, load_moex_hl
    off_v = df["cny_rub"].to_numpy(float)
    lp = np.log(off_v)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    moex = load_moex()
    hl = load_moex_hl()
    pred = MoexDirectionPredictor()
    pred.attach_moex(dates, lp, moex, hl_map=hl)
    return pred, lp, dates


def run_direction_backtest(df, oil_df=None, sentiment_df=None, rate_df=None):
    """MOEX 增强方向 walk-forward 回测。

    置信度采用**扩展窗样本外(OOS)校准**: 在 t 时刻给预测定置信时, 只用结果
    已实现的历史窗口(j+N<=t)统计各 |z| 桶命中率; 桶内样本不足 20 个时回退内置
    默认表。方向符号本身不依赖校准表(z 的正负), 故全样本 accuracy 口径不变;
    受影响的只有"高置信档命中率"—— 旧实现用全样本(含被评估窗口)校准再回头
    选档, 该数字偏乐观。生产预测(save_forecasts)仍用全样本 calibration.json,
    因为实盘时点全部历史结果均已实现, 不存在前视。
    """
    from app.data.features import build_features
    from app.models.meanrev_dir import _DEFAULT_MEANREV
    pred, lp, _ = _build_moex_ctx(df)
    m = len(lp)
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float); feat_names = list(Fdf.columns)
    first = max(config.MIN_TRAIN, int(valid[0]) if len(valid) else 0)
    thr = config.CONFIDENT_THRESHOLD
    horizons = {}
    for N in config.N_HORIZONS:
        last = m - 1 - N
        starts = range(first, last + 1)
        correct = conf_c = conf_t = total = 0
        mx_c = mx_t = mx_cc = mx_ct = 0  # MOEX 覆盖期
        up = 0
        # OOS 校准计数: 仅由"已实现"窗口更新
        b_hits = {bk: 0 for bk, _ in _Z_BUCKETS}
        b_tot = {bk: 0 for bk, _ in _Z_BUCKETS}
        mr_hits = mr_tot = 0
        # 记录每个窗口的预测, 供 N 步后实现时归档: (bucket, pred, signal)
        records = {}
        for i in starts:
            # 1) 实现窗口 j=i-N(若存在): 此刻 lp[i]=lp[j+N] 首次可得
            j = i - N
            rec = records.pop(j, None)
            if rec is not None:
                bk, pred_j, sig_j = rec
                actual_j = 1 if lp[i] > lp[j] else 0
                if sig_j == "moex_dev" and bk is not None:
                    b_tot[bk] += 1
                    b_hits[bk] += int(pred_j == actual_j)
                elif sig_j == "meanrev_strong":
                    mr_tot += 1
                    mr_hits += int(pred_j == actual_j)
            # 2) 用只含已实现窗口的校准表给 t=i 的预测定置信
            cal_tbl = _oos_bucket_table(N, b_hits, b_tot)
            mr_conf = ({N: round(mr_hits / mr_tot, 4)} if mr_tot >= 20
                       else {N: _DEFAULT_MEANREV.get(N, 0.55)})
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid, "feat_names": feat_names}
            r = pred.predict_direction(ctx, N, cal_tbl=cal_tbl, cap_tbl=cal_tbl,
                                       meanrev_conf=mr_conf)
            actual = 1 if lp[i + N] > lp[i] else 0
            up += int(actual == 1)
            hit = r["prediction"] == actual
            correct += int(hit); total += 1
            if r["confidence"] > thr:
                conf_c += int(hit); conf_t += 1
            sig = r.get("signal")
            if sig == "moex_dev":
                mx_c += int(hit); mx_t += 1
                if r["confidence"] > thr:
                    mx_cc += int(hit); mx_ct += 1
            records[i] = (r.get("z_bucket") if sig == "moex_dev" else None,
                          r["prediction"],
                          sig if sig in ("moex_dev", "meanrev_strong") else "other")
        bl = max(up, total - up) / total if total else 0.5
        horizons[str(N)] = {
            "N": N, "windows": total, "accuracy": round(correct / total, 4) if total else 0,
            "confident_accuracy": round(conf_c / conf_t, 4) if conf_t else 0,
            "confident_windows": conf_t,
            "confident_ratio": round(conf_t / total, 4) if total else 0,
            "confident_threshold": thr,
            "baseline_always_majority": round(bl, 4),
            "moex_accuracy": round(mx_c / mx_t, 4) if mx_t else 0,
            "moex_windows": mx_t,
            "moex_confident_accuracy": round(mx_cc / mx_ct, 4) if mx_ct else 0,
            "moex_confident_windows": mx_ct,
        }
    return {"meta": {"as_of": min(df.index[-1].date(), config.msk_today()).isoformat(),
                     "rows": m,
                     "model": "MoexDirectionPredictor",
                     "calibration": "expanding_window_oos",
                     "confident_threshold": thr},
            "horizons": horizons}


def _pool_to_monotonic(cal_n, bucket_hits, bucket_total):
    """相邻桶非单调时的处理(原地修改 cal_n):

    用两比例检验判断差异是否真实:
      * 统计上不可区分(|z|<1.96, 双侧) -> 两档**样本量加权池化**。
        池化是双向收敛, 且样本合并后方差更小, 比"把高档抬到低档的值"更可信。
      * 差异确实可区分 -> **保留各自实测值**, 如实显示非单调, 不掩盖。
    旧实现无条件把高档抬到低档的值, 只会**单向上抬**(N=7 z10 曾被虚高 +7.6pp)。
    """
    monotonic_keys = ["z00", "z05", "z10", "z15"]
    for _ in range(len(monotonic_keys)):   # 池化只会合并, 有界重复即可收敛
        merged = False
        for j in range(1, len(monotonic_keys)):
            lo, hi = monotonic_keys[j - 1], monotonic_keys[j]
            if cal_n[hi] >= cal_n[lo]:
                continue
            n_lo, n_hi = bucket_total[lo], bucket_total[hi]
            if n_lo < 20 or n_hi < 20:
                continue       # 样本不足者已回退默认值, 不参与池化
            p_lo, p_hi = bucket_hits[lo] / n_lo, bucket_hits[hi] / n_hi
            se = float(np.sqrt(p_lo * (1 - p_lo) / n_lo + p_hi * (1 - p_hi) / n_hi))
            if se <= 0 or abs((p_hi - p_lo) / se) >= 1.96:
                continue       # 差异真实 -> 保留实测值
            cal_n[lo] = cal_n[hi] = round(
                (bucket_hits[lo] + bucket_hits[hi]) / (n_lo + n_hi), 4)
            merged = True
        if not merged:
            break
    return cal_n


def calibrate_moex_z(df, oil_df=None, sentiment_df=None, rate_df=None):
    """MOEX z 分档校准:统计各 N 各 |z| 档实际命中率, 写入 calibration.json。

    与 run_direction_backtest 类似, 但只关注 MOEX 信号窗口的 z 分档命中率,
    输出供 MoexDirectionPredictor 运行时加载的动态校准表。
    """
    from app.data.features import build_features
    config.DATA_DIR.mkdir(exist_ok=True)
    pred, lp, _ = _build_moex_ctx(df)
    m = len(lp)
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float)
    feat_names = list(Fdf.columns)
    first = max(config.MIN_TRAIN, int(valid[0]) if len(valid) else 0)

    cal = {}
    cap = {}

    for N in config.N_HORIZONS:
        last = m - 1 - N
        if first > last:
            continue
        # 每档的命中/总数(累积分桶, 口径同 _Z_BUCKETS)
        bucket_hits = {k: 0 for k, _ in _Z_BUCKETS}
        bucket_total = {k: 0 for k, _ in _Z_BUCKETS}

        for i in range(first, last + 1):
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid, "feat_names": feat_names}
            r = pred.predict_direction(ctx, N)
            if r.get("signal") != "moex_dev":
                continue
            actual = 1 if lp[i + N] > lp[i] else 0
            hit = r["prediction"] == actual
            # 归入对应档
            bk = r.get("z_bucket") or _bucket_key(abs(r.get("z", 0)))
            bucket_hits[bk] += int(hit)
            bucket_total[bk] += 1

        # 计算各档命中率(样本不足回退默认值), 再做统计检验池化
        default_tbl = _DEFAULT_CAL.get(N, _DEFAULT_CAL[7])
        cal_n = {bk: (round(bucket_hits[bk] / bucket_total[bk], 4)
                      if bucket_total[bk] >= 20 else default_tbl.get(bk, 0.6))
                 for bk, _ in _Z_BUCKETS}
        _pool_to_monotonic(cal_n, bucket_hits, bucket_total)
        cal[N] = cal_n

        # 分档 cap = 该桶校准准确率
        cap[N] = dict(cal_n)

    # === MeanRev strong 信号校准: 统计各 N 下 meanrev_strong 的实际命中率 ===
    from app.models.meanrev_dir import MeanRevDirectionPredictor
    mr_pred = MeanRevDirectionPredictor()
    mr_hits = {N: 0 for N in config.N_HORIZONS}
    mr_totals = {N: 0 for N in config.N_HORIZONS}
    for N in config.N_HORIZONS:
        last = m - 1 - N
        if first > last:
            continue
        for i in range(first, last + 1):
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid, "feat_names": feat_names}
            r = mr_pred.predict_direction(ctx, N)
            if r.get("signal") != "meanrev_strong":
                continue
            actual = 1 if lp[i + N] > lp[i] else 0
            hit = r["prediction"] == actual
            mr_hits[N] += int(hit)
            mr_totals[N] += 1
    meanrev_conf = {}
    for N in config.N_HORIZONS:
        if mr_totals[N] >= 20:
            meanrev_conf[str(N)] = round(mr_hits[N] / mr_totals[N], 4)
            log.info("MeanRev N=%d strong: %d/%d = %.1f%%", N,
                     mr_hits[N], mr_totals[N], mr_hits[N] / mr_totals[N] * 100)

    result = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_ts": time.time(),
        "cal": {str(k): v for k, v in cal.items()},
        "cap": {str(k): v for k, v in cap.items()},
        "meanrev": meanrev_conf,
    }
    with open(_CALIBRATION_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    log.info("校准结果已写入 %s", _CALIBRATION_PATH)
    return result
