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
    if not direction or direction.get("prediction") not in (0, 1):
        return []
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
    for k, dt in enumerate(dates):
        frac = (k + 1) / n
        path = frac ** 0.7
        p50 = float(np.exp(base + med * path))
        # 有 daily_vol: 1*sigma*sqrt(k) 的波动扩散; 兜底: 旧的经验喇叭口
        if daily_vol and daily_vol > 0:
            halfband = daily_vol * math.sqrt(k + 1)
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
    predictor.attach_moex(_dates, lp, load_moex(), hl_map=load_moex_hl())

    cur_rate = float(np.exp(lp[-1]))
    base_date = df.index[-1].date()
    daily_vol = recent_daily_vol(lp)

    for N in config.N_HORIZONS:
        dr = predictor.predict_direction(ctx, N)
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


def load_forecast(N: int) -> dict | None:
    path = config.FORECAST_JSONS.get(N)
    if path is None or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None   # 文件正被调度器重写: 本轮读不到, 下次刷新再取
