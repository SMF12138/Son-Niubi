"""当前时点预测:用方向模型生成未来 N 天的价格投影(无前视)。

输出方向结论 + 基于方向的投影路径(中位 + 不确定性带),
全部换算回"1 人民币 = X 卢布"的价格刻度,映射到未来自然日期。
"""
import json

import numpy as np

from app import config
from app.data.calendar import future_trading_dates
from app.data.features import build_features


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

    for N in config.N_HORIZONS:
        dr = predictor.predict_direction(ctx, N)
        fdates = future_trading_dates(base_date, N)
        pred = dr.get("prediction", 0)
        conf = dr.get("confidence", 0.5)

        # 基于方向的投影路径: 方向 × 置信度 × 历史幅度
        sign = -1.0 if pred == 0 else 1.0
        sig_w = max(0.5, min(1.0, (conf - 0.5) * 2))
        med = sign * 0.0161 * sig_w
        base = np.log(cur_rate)
        npts = len(fdates)

        forecast = []
        import math
        for k, dt in enumerate(fdates):
            frac = (k + 1) / npts
            path = frac ** 0.7
            p50 = float(np.exp(base + med * path))
            halfband = 0.012 * math.sqrt(frac) * (1.0 + (N / 90.0))
            lo = float(np.exp(base + med * path - halfband))
            hi = float(np.exp(base + med * path + halfband))
            forecast.append({"date": dt.isoformat(), "rate": round(p50, 4),
                             "low": round(min(lo, p50), 4),
                             "high": round(max(hi, p50), 4)})

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
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fc, f, ensure_ascii=False, indent=1)


def load_forecast(N: int) -> dict | None:
    path = config.FORECAST_JSONS.get(N)
    if path is None or not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
