"""当前时点预测:用方向模型生成未来 N 天的价格投影(无前视)。

输出方向结论 + 基于方向的投影路径(中位 + 不确定性带),
全部换算回"1 人民币 = X 卢布"的价格刻度,映射到未来自然日期。
"""
import json
import math
from datetime import datetime

import numpy as np

from app import config
from app.data import store
from app.data.calendar import future_trading_dates
from app.data.features import build_features

# 长周期带宽校准: ±1σ 带实测历史覆盖率仅 63~67%(肥尾+波动聚集, n≈3900),
# 乘以下列系数后达到名义 80% 覆盖(全历史 |ΔP|/(σ√k) 经验 80% 分位)。
# 波动率带宽比方向信号平稳得多, 校准跨期稳定; 7 日卡保持 ±1σ 语义不动。
BAND_COVERAGE = 0.80
BAND_COVERAGE_MULT = {30: 1.51, 60: 1.81, 90: 2.02}


def _anchor_disp(direction, horizon=7):
    """单个模型在其到期日的目标位移(对数): "从今天到该周期到期"的中位对数变动。

    幅度 = 0.0161*sig_w*sqrt(horizon/7):
      - sig_w 随把握度连续变化(无下限钳死), 55%->0.1, 70%->0.4, 100%->1;
      - sqrt(horizon/7) 让长周期的示意位移大于短周期(与波动带 sqrt(k) 扩散
        同口径), 否则拼接后同方向的各期限锚点重合、曲线中途变平。
    弱信号(<0.55)/中性/无方向 -> 0(锚点贴当前价, 该段走平)。
    幅度只是"方向示意", 非点估计; 真正有数据依据的是波动带。
    """
    conf = direction.get("confidence") if direction else None
    weak = isinstance(conf, (int, float)) and conf < 0.55
    if not direction or direction.get("prediction") not in (0, 1) or weak:
        return 0.0
    sign_dir = -1.0 if direction["prediction"] == 0 else 1.0
    sig_w = min(1.0, max(0.0, (conf - 0.5) * 2))
    scale = math.sqrt(max(horizon, 1) / 7.0)
    return sign_dir * 0.0161 * sig_w * scale


def build_term_projection(cur_rate, anchors, dates, daily_vol=None):
    """期限结构拼接投影: 多个周期模型各自给出"到期日锚点", 锚点间插值。

    例: 60 日图锚点 = [(0,0),(7,d7),(30,d30),(60,d60)], 第 1-7 天跟随
    7 日模型、8-30 天跟随 30 日模型、31-60 天跟随 60 日模型——短周期看跌、
    长周期看涨时曲线自然先跌后涨, 而不是用长周期一个方向抹掉前段。

    Args:
        cur_rate: 当前汇率
        anchors: [(交易日偏移k, 对数位移disp)] 已排序, 必须以 (0,0.0) 起、
                 末点 k == len(dates)
        dates: 未来交易日列表
        daily_vol: 近窗日波动率, 带宽按 sqrt(k) 扩散
    """
    n = len(dates)
    if n == 0 or not anchors:
        return []
    base = np.log(cur_rate)
    mult = BAND_COVERAGE_MULT.get(n, 1.0)
    forecast = []
    for kk, dt_ in enumerate(dates):
        k = kk + 1  # 1-based 交易日序号
        # 定位 k 所在锚点段 [a, b]
        a, da = 0, 0.0
        b, db = anchors[-1]
        for (a0, d0), (b0, d1) in zip(anchors[:-1], anchors[1:]):
            if a0 < k <= b0:
                a, da, b, db = a0, d0, b0, d1
                break
        frac = (k - a) / (b - a) if b > a else 1.0
        disp = da + (db - da) * frac ** 0.7
        if daily_vol and daily_vol > 0:
            halfband = daily_vol * math.sqrt(k) * mult
        else:
            halfband = 0.012 * math.sqrt(k / n) * (1.0 + (n / 90.0))
        p50 = float(np.exp(base + disp))
        lo = float(np.exp(base + disp - halfband))
        hi = float(np.exp(base + disp + halfband))
        dt_str = dt_.isoformat() if hasattr(dt_, "isoformat") else str(dt_)
        forecast.append({"date": dt_str, "rate": round(p50, 4),
                         "low": round(min(lo, p50), 4),
                         "high": round(max(hi, p50), 4)})
    return forecast


def build_projection(cur_rate, direction, dates, daily_vol=None):
    """基于方向结论生成投影路径(中位 + 不确定性带)。

    单周期便捷封装: 锚点只有 (今天,0) 与 (N天,该模型目标位移)。
    多周期期限结构拼接见 build_term_projection。
    """
    # 弱信号/中性: 中位线贴当前价, 只给对称波动区间
    conf = direction.get("confidence") if direction else None
    weak = isinstance(conf, (int, float)) and conf < 0.55
    if not direction or direction.get("prediction") not in (0, 1):
        # 无任何方向结论(连中性标记都没有): 不画预测
        if not direction or not direction.get("neutral"):
            return []
        anchors = [(0, 0.0), (len(dates), 0.0)]
    elif weak:
        anchors = [(0, 0.0), (len(dates), 0.0)]
    else:
        anchors = [(0, 0.0), (len(dates), _anchor_disp(direction, len(dates)))]
    return build_term_projection(cur_rate, anchors, dates, daily_vol=daily_vol)


def recent_daily_vol(lp, window=60):
    """近 window 个日对数收益的标准差, 供投影带宽标定; 数据不足返回 None。"""
    if lp is None or len(lp) < 20:
        return None
    rets = np.diff(lp[-window - 1:])
    rets = rets[np.isfinite(rets)]
    if len(rets) < 20:
        return None
    return float(np.std(rets, ddof=1))


def save_forecasts(df, oil_df=None, sentiment_df=None, rate_df=None) -> None:
    """生成当前时点预测并写入 forecast_*.json。"""
    from app.models.moex_dir import MoexDirectionPredictor
    from app.models.longhorizon import predict_longhorizon, load_longhorizon_result
    from app.data.moex_rates import load_moex, load_moex_hl

    config.DATA_DIR.mkdir(exist_ok=True)
    lp = np.log(df["cny_rub"].to_numpy(dtype=float))
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float)
    feat_names = list(Fdf.columns)
    ctx = {"lp": lp, "i": len(lp) - 1, "Xf": Xf, "valid": valid, "feat_names": feat_names}
    predictor = MoexDirectionPredictor()
    _dates = [d.strftime("%Y-%m-%d") for d in df.index]
    moex_map = load_moex()
    predictor.attach_moex(_dates, lp, moex_map, hl_map=load_moex_hl())
    lh_result = load_longhorizon_result()   # 缺失时 30/60/90 全部安全降级为中性

    cur_rate = float(np.exp(lp[-1]))
    base_date = df.index[-1].date()
    daily_vol = recent_daily_vol(lp)

    drs = {}
    fdates_map = {}
    for N in config.N_HORIZONS:
        if N == 7:
            dr = predictor.predict_direction(ctx, N)
        else:
            dr = predict_longhorizon(lp, _dates, moex_map, N, result=lh_result,
                                     oil_df=oil_df)
        drs[N] = dr
        fdates_map[N] = future_trading_dates(base_date, N)

    # 期限结构拼接: 长周期图必须逐段服从更短周期模型——
    # 7日看跌/30日看涨时, 30日图先跌后涨, 而不是被长周期一个方向抹平。
    # future_trading_dates 是同一日历的确定性推演, 长周期日期列的前 k 个
    # 与短周期完全一致, 锚点可直接按交易日序号拼接。
    for N in config.N_HORIZONS:
        fdates = fdates_map[N]
        shorter = [h for h in config.N_HORIZONS if h < N]
        if not shorter:
            forecast = build_projection(cur_rate, drs[N], fdates,
                                        daily_vol=daily_vol)
        else:
            anchors = [(0, 0.0)]
            for h in shorter:
                anchors.append((h, _anchor_disp(drs[h], h)))
            anchors.append((N, _anchor_disp(drs[N], N)))
            forecast = build_term_projection(cur_rate, anchors, fdates,
                                             daily_vol=daily_vol)

        fc = {
            "N": N,
            "as_of": df.index[-1].isoformat(),
            "refreshed_at": datetime.now().isoformat(),
            "base_rate": round(cur_rate, 4),
            "forecast_dates": [d.isoformat() for d in fdates],
            "forecast": forecast,
            "direction": drs[N],
            "term_anchors": [
                {"horizon": h, "prediction": (drs[h] or {}).get("prediction"),
                 "confidence": (drs[h] or {}).get("confidence")}
                for h in shorter + [N]
            ] if shorter else [],
        }

        path = config.FORECAST_JSONS.get(N)
        if path:
            store.write_json_atomic(path, fc)

    # 预测永久留档 + 影子策略 + 到期结算(快层每60秒重跑, 内部 UNIQUE 幂等)。
    # 留档故障不得影响看板: 记日志即可。
    try:
        from app.models.prediction_ledger import record_forecast_day
        record_forecast_day(df, drs, oil_df=oil_df)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("prediction ledger 记录失败: %s", e)


def load_forecast(N: int) -> dict | None:
    path = config.FORECAST_JSONS.get(N)
    if path is None or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None   # 文件正被调度器重写: 本轮读不到, 下次刷新再取
