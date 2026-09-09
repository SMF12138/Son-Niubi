"""当前时点预测:用全量数据重训成员,集成权重取自回测最终命中率(无前视)。

输出对数路径的分位数带(p10/p50/p90)、集成见顶日、逐日见顶概率与成员明细,
全部换算回"1 人民币 = X 卢布"的价格刻度,并映射到未来自然日期(近似交易日历)。
"""
import datetime as dt
import json

import numpy as np

from app import config
from app.backtest.engine import _make_models
from app.data.calendar import future_trading_dates
from app.data.features import build_features
from app.models.ensemble import (
    combine_paths,
    earliest_argmax,
    peak_probability_by_day,
    weighted_path_quantiles,
)


def compute_forecast(df, N: int, weights: dict | None = None,
                     models: list | None = None, oil_df=None, sentiment_df=None,
                     rate_df=None) -> dict:
    """df 为升序 rates DataFrame;weights 为 {成员名: 权重}。"""
    lp = np.log(df["cny_rub"].to_numpy(dtype=float))
    dates = df.index
    i = len(lp) - 1
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(dtype=float)

    models = models or _make_models()
    names = [mo.name for mo in models]
    ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid}
    paths = {mo.name: np.asarray(mo.predict(ctx, N), dtype=float) for mo in models}

    if weights is None:
        weights = {nm: 1.0 / len(names) for nm in names}
    weights = {nm: weights.get(nm, 0.0) for nm in names}
    mid_log = combine_paths(paths, weights)
    q10, q50, q90 = weighted_path_quantiles(paths, weights)
    prob = peak_probability_by_day(paths, weights, N)

    peak_k = earliest_argmax(mid_log)  # 0-based
    base_date = dates[i].date()
    fdates = future_trading_dates(base_date, N)

    def _levels(arr: np.ndarray) -> list[float]:
        return [round(float(np.exp(v)), 4) for v in arr]

    models_out = []
    for nm in names:
        k = earliest_argmax(paths[nm])
        models_out.append({
            "name": nm,
            "weight": round(weights[nm], 4),
            "peak_day": k + 1,
            "peak_date": fdates[k].isoformat(),
            "peak_level": round(float(np.exp(paths[nm][k])), 4),
        })
    return {
        "N": N,
        "as_of": dates[i].isoformat(),
        "base_rate": round(float(np.exp(lp[i])), 4),
        "forecast_dates": [d.isoformat() for d in fdates],
        "low": _levels(q10),
        "mid": _levels(q50),
        "high": _levels(q90),
        "ensemble_mid": _levels(mid_log),
        "peak": {
            "day": peak_k + 1,
            "date": fdates[peak_k].isoformat(),
            "level": round(float(np.exp(mid_log[peak_k])), 4),
        },
        "prob_by_day": [round(p, 4) for p in prob],
        "models": models_out,
    }


def save_forecasts(df, report: dict | None, oil_df=None, sentiment_df=None, rate_df=None) -> None:
    from app.models.moex_dir import MoexDirectionPredictor
    from app.data.moex_rates import load_moex, load_moex_hl

    config.DATA_DIR.mkdir(exist_ok=True)
    lp = np.log(df["cny_rub"].to_numpy(dtype=float))
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(dtype=float)
    feat_names = list(Fdf.columns)
    ctx = {"lp": lp, "i": len(lp) - 1, "Xf": Xf, "valid": valid, "feat_names": feat_names}
    predictor = MoexDirectionPredictor()
    _dates = [d.strftime("%Y-%m-%d") for d in df.index]
    predictor.attach_moex(_dates, lp, load_moex(), hl_map=load_moex_hl())

    for N in config.N_HORIZONS:
        weights = None
        if report:
            h = report["horizons"].get(str(N), {})
            weights = h.get("final_weights")
        fc = compute_forecast(df, N, weights=weights, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
        dr = predictor.predict_direction(ctx, N)
        fc["direction"] = dr
        # direction(方向分类器) 与 ensemble(点位/见顶) 是两个独立模型;
        # 产品对外的方向结论只采用经方向回测验证的 direction。
        # 两者可能不一致(如 ensemble 看涨但 direction 看跌),这是设计如此,不是 bug。
        fc["model_note"] = ("方向(direction)与价格路径(ensemble)是独立模型,可能不一致。"
                            "前端展示以 direction 为准。")

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
