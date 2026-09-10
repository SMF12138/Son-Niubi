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
            log.info("校准文件过期(%.1f小时前), 用默认值", age / 3600)
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


class MoexDirectionPredictor:
    name = "MOEX-DIR"

    def __init__(self):
        self._fallback = MeanRevDirectionPredictor()
        self._moex_dev = None      # {row_index: raw_dev}
        self._dev_hist = None      # 升序 (row_index, raw_dev) 用于滚动标准化
        self._moex_close = None    # {row_index: close}
        self._moex_hlr = None      # {row_index: (high-low)/close 日内幅
        self._z = None             # {row_index: 滚动 z(win150)} 用于连续偏离确认}
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
        moex_map: {date_str: moex_close}; hl_map: {date_str: (close,high,low)} 可选。"""
        dev = {}
        seq = []
        close = {}
        hlr = {}
        for i, d in enumerate(dates):
            mv = moex_map.get(d)
            if mv is not None and mv > 0:
                rd = float(np.log(mv) - off_lp[i])
                dev[i] = rd
                seq.append((i, rd))
                close[i] = mv
                if hl_map and d in hl_map:
                    c, h, lo = hl_map[d]
                    if c and h and lo:
                        hlr[i] = (h - lo) / c
        self._moex_dev = dev
        self._dev_hist = seq
        self._moex_close = close
        self._moex_hlr = hlr
        # 预计算滚动 z 序列(win=150, 无前视), 用于“连续偏离天数”确认
        self._z = {}
        devs = [rd for (_, rd) in seq]
        idxs = [j for (j, _) in seq]
        for t in range(len(seq)):
            if t + 1 >= 60:
                a = np.array(devs[max(0, t + 1 - 150):t + 1])
                self._z[idxs[t]] = (devs[t] - a.mean()) / (a.std() + 1e-9)

    def predict_direction(self, ctx, N):
        i = ctx["i"]

        # === 30天: 优先用均值回复 + USD/RUB 确认 ===
        if 30 <= N < 90:
            r = self._fallback.predict_direction(ctx, N)
            if r.get("signal") == "meanrev_strong":
                return r
            # 均值回复弱信号时,如果也有 MOEX 偏离,混合判断
            # (不直接用 MOEX 做主信号,只作参考)

        # === 7天 / 30天无强信号 / 90天: MOEX 偏离(核心信号) ===
        # 有 MOEX 偏离(当日或最近 ≤3 交易日) → 用它(滚动标准化, 无前视)
        dev_i = None
        if self._moex_dev is not None:
            if i in self._moex_dev:
                dev_i = self._moex_dev[i]
            else:
                # 回退到 <=i 的最近 MOEX 日(间隔 ≤3), 偏离有持续性
                for j in range(i - 1, max(i - 4, -1), -1):
                    if j in self._moex_dev:
                        dev_i = self._moex_dev[j]
                        break
        if dev_i is not None:
            past = [rd for (j, rd) in self._dev_hist if j <= i]
            if len(past) >= 60:
                arr = np.array(past[-150:])   # 150天窗:实测优于100/200
                mu = arr.mean(); sd = arr.std() + 1e-9
                z = (dev_i - mu) / sd
                pred = 1 if z > 0 else 0
                az = abs(z)

                # === 多重确认信号(均当日可得, 无前视) ===
                confirms = 0
                # 确认1: MOEX 5日动量与 zdev 同向
                if self._moex_close:
                    cj = [j for j in range(i, max(i - 6, -1), -1) if j in self._moex_close]
                    if len(cj) >= 2:
                        c_now = self._moex_close[cj[0]]
                        c_prev = self._moex_close[cj[-1]]
                        mom = np.log(c_now) - np.log(c_prev)
                        if (mom > 0) == (z > 0):
                            confirms += 1
                # 确认2: 偏离加深(dev 近5日变化与 z 同号)
                past5 = [rd for (j, rd) in self._dev_hist if j <= i][-6:]
                if len(past5) >= 6:
                    dz = past5[-1] - past5[0]
                    if np.sign(dz) == np.sign(z):
                        confirms += 1
                # 确认3: 高波动日(日内幅 ≥ 历史中位)
                if self._moex_hlr and i in self._moex_hlr:
                    hist_hlr = [v for (j), v in self._moex_hlr.items() if j <= i]
                    if len(hist_hlr) >= 30:
                        if self._moex_hlr[i] >= float(np.median(hist_hlr)):
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
                if self._z is not None and self._moex_dev is not None:
                    zdays = [j for (j, _) in self._dev_hist if j <= i][-6:]
                    streak = 0
                    for j in reversed(zdays):
                        zj = self._z.get(j)
                        if zj is not None and np.sign(zj) == np.sign(z):
                            streak += 1
                        else:
                            break
                    if streak >= 2:
                        confirms += 1

                # === 置信度: 按 horizon 分别校准(动态 or 默认) ===
                cal_n = min(self._cal.keys(), key=lambda k: abs(k - N))
                tbl = self._cal[cal_n]
                cap_tbl = self._cap.get(cal_n, _DEFAULT_CAP.get(cal_n, {}))
                if az > 1.5:
                    bucket_key = "z15"
                elif az > 1.0:
                    bucket_key = "z10"
                elif az > 0.5:
                    bucket_key = "z05"
                else:
                    bucket_key = "z00"
                conf = tbl[bucket_key]
                cap = cap_tbl.get(bucket_key, 0.7)
                if confirms >= 3:
                    conf = min(conf + 0.02, cap)
                elif confirms >= 2:
                    conf = min(conf + 0.01, cap)
                conf = max(0.5, min(conf, cap))
                pu = conf if pred == 1 else 1 - conf
                return {"prediction": pred, "confidence": round(conf, 3),
                        "prob_up": round(pu, 3), "prob_down": round(1 - pu, 3),
                        "signal": "moex_dev", "z": round(float(z), 3),
                        "confirms": int(confirms), "horizon": N}
        # 无 MOEX → 退回均值回复
        r = self._fallback.predict_direction(ctx, N)
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
    """MOEX 增强方向 walk-forward 回测, 形状与 meanrev_dir 一致。"""
    from app.data.features import build_features
    pred, lp, _ = _build_moex_ctx(df)
    m = len(lp)
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float); feat_names = list(Fdf.columns)
    first = max(config.MIN_TRAIN, int(valid[0]) if len(valid) else 0)
    horizons = {}
    for N in config.N_HORIZONS:
        last = m - 1 - N
        starts = [i for i in range(first, last + 1)]
        correct = conf_c = conf_t = total = 0
        mx_c = mx_t = mx_cc = mx_ct = 0  # MOEX 覆盖期
        for i in starts:
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid, "feat_names": feat_names}
            r = pred.predict_direction(ctx, N)
            actual = 1 if lp[i + N] > lp[i] else 0
            hit = r["prediction"] == actual
            correct += int(hit); total += 1
            if r["confidence"] > 0.6:
                conf_c += int(hit); conf_t += 1
            if r.get("signal") == "moex_dev":
                mx_c += int(hit); mx_t += 1
                if r["confidence"] > 0.6:
                    mx_cc += int(hit); mx_ct += 1
        up = sum(1 for i in starts if lp[i + N] > lp[i])
        bl = max(up, total - up) / total if total else 0.5
        horizons[str(N)] = {
            "N": N, "windows": total, "accuracy": round(correct / total, 4) if total else 0,
            "confident_accuracy": round(conf_c / conf_t, 4) if conf_t else 0,
            "confident_windows": conf_t,
            "confident_ratio": round(conf_t / total, 4) if total else 0,
            "baseline_always_majority": round(bl, 4),
            "moex_accuracy": round(mx_c / mx_t, 4) if mx_t else 0,
            "moex_windows": mx_t,
            "moex_confident_accuracy": round(mx_cc / mx_ct, 4) if mx_ct else 0,
        }
    return {"meta": {"as_of": df.index[-1].isoformat(), "rows": m,
                     "model": "MoexDirectionPredictor"},
            "horizons": horizons}


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

    # z 分档边界
    z_buckets = [("z15", 1.5), ("z10", 1.0), ("z05", 0.5), ("z00", 0.0)]

    cal = {}
    cap = {}

    for N in config.N_HORIZONS:
        last = m - 1 - N
        if first > last:
            continue
        # 每档的命中/总数
        bucket_hits = {k: 0 for k, _ in z_buckets}
        bucket_total = {k: 0 for k, _ in z_buckets}

        for i in range(first, last + 1):
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid, "feat_names": feat_names}
            r = pred.predict_direction(ctx, N)
            if r.get("signal") != "moex_dev":
                continue
            actual = 1 if lp[i + N] > lp[i] else 0
            hit = r["prediction"] == actual
            az = abs(r.get("z", 0))
            # 归入对应档
            for bk, threshold in z_buckets:
                if az > threshold:
                    bucket_hits[bk] += int(hit)
                    bucket_total[bk] += 1
                    break

        # 计算各档命中率
        cal_n = {}
        for bk, _ in z_buckets:
            if bucket_total[bk] >= 20:
                cal_n[bk] = round(bucket_hits[bk] / bucket_total[bk], 4)
            else:
                # 样本不足, 回退默认值
                default_tbl = _DEFAULT_CAL.get(N, _DEFAULT_CAL[7])
                cal_n[bk] = default_tbl.get(bk, 0.6)

        # 强制单调: z00 <= z05 <= z10 <= z15(偏离越大置信越高)
        monotonic_keys = ["z00", "z05", "z10", "z15"]
        for j in range(1, len(monotonic_keys)):
            prev_k, cur_k = monotonic_keys[j - 1], monotonic_keys[j]
            if cal_n[cur_k] < cal_n[prev_k]:
                cal_n[cur_k] = cal_n[prev_k]
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
