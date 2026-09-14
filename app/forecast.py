"""当前时点预测:用方向模型生成未来 N 天的价格投影(无前视)。

输出方向结论 + 基于方向的投影路径(中位 + 不确定性带),
全部换算回"1 人民币 = X 卢布"的价格刻度,映射到未来自然日期。
"""
import json
import math

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


def build_projection(cur_rate, direction, dates, daily_vol=None):
    """基于方向结论生成投影路径(中位 + 不确定性带)。

    Args:
        cur_rate: 当前汇率(1 CNY = X RUB)
        direction: 方向预测 dict (prediction, confidence, ...)
        dates: 未来日期列表(字符串或 date 对象)
        daily_vol: 近窗日对数收益标准差; 给定后不确定性带按 sqrt(k) 波动扩散
            (1 个 sigma, 非严格统计置信区间)。None 时回退历史常数带宽(仅兜底)。

    注意: 中位线的幅度(0.0161*sig_w)只是"方向示意", 非点估计;
    真正有数据依据的是带宽随已实现波动率的缩放。
    """
    # 弱信号口径(与前端一致): 把握度 < 55% 中位线不偏移, 只给对称波动区间
    conf = direction.get("confidence") if direction else None
    weak = isinstance(conf, (int, float)) and conf < 0.55
    if (not direction or direction.get("prediction") not in (0, 1) or weak):
        if not direction or (not direction.get("neutral") and not weak):
            return []
        n = len(dates)
        if n == 0:
            return []
        base = np.log(cur_rate)
        mult = BAND_COVERAGE_MULT.get(n, 1.0)
        forecast = []
        for k, dt in enumerate(dates):
            frac = (k + 1) / n
            if daily_vol and daily_vol > 0:
                halfband = daily_vol * math.sqrt(k + 1) * mult
            else:
                halfband = 0.012 * math.sqrt(frac) * (1.0 + (n / 90.0))
            p50 = cur_rate
            lo = float(np.exp(base - halfband))
            hi = float(np.exp(base + halfband))
            dt_str = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
            forecast.append({"date": dt_str, "rate": round(p50, 4),
                             "low": round(lo, 4), "high": round(hi, 4)})
        return forecast
    pred = direction["prediction"]
    conf = direction.get("confidence", 0.55)
    n = len(dates)
    if n == 0:
        return []

    sign_dir = -1.0 if pred == 0 else 1.0
    sig_w = max(0.5, min(1.0, (conf - 0.5) * 2))
    base = np.log(cur_rate)
    med = sign_dir * 0.0161 * sig_w

    forecast = []
    mult = BAND_COVERAGE_MULT.get(n, 1.0)
    for k, dt in enumerate(dates):
        frac = (k + 1) / n
        path = frac ** 0.7
        p50 = float(np.exp(base + med * path))
        # 有 daily_vol: σ*sqrt(k)*校准系数 的波动扩散; 兜底: 旧的经验喇叭口
        if daily_vol and daily_vol > 0:
            halfband = daily_vol * math.sqrt(k + 1) * mult
        else:
            halfband = 0.012 * math.sqrt(frac) * (1.0 + (n / 90.0))
        lo = float(np.exp(base + med * path - halfband))
        hi = float(np.exp(base + med * path + halfband))
        dt_str = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        forecast.append({"date": dt_str, "rate": round(p50, 4),
                         "low": round(min(lo, p50), 4),
                         "high": round(max(hi, p50), 4)})
    return forecast


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
    for N in config.N_HORIZONS:
        if N == 7:
            dr = predictor.predict_direction(ctx, N)
        else:
            dr = predict_longhorizon(lp, _dates, moex_map, N, result=lh_result,
                                     oil_df=oil_df)
        drs[N] = dr
        fdates = future_trading_dates(base_date, N)

        forecast = build_projection(cur_rate, dr, fdates, daily_vol=daily_vol)

        fc = {
            "N": N,
            "as_of": df.index[-1].isoformat(),
            "base_rate": round(cur_rate, 4),
            "forecast_dates": [d.isoformat() for d in fdates],
            "forecast": forecast,
            "direction": dr,
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
