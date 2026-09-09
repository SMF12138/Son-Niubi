"""严格 walk-forward 回测。

对每个评估起点 i(每 REFIT_STRIDE 个交易日一个):
- 只用 ≤i 的数据构造路径预测,窗口 = 观测行 i+1..i+N(真实交易日,不依赖推算日历);
- 实际见顶日 p* = 窗口内最早达到最大值的行偏移;各成员/集成预测见顶日 p̂ 同理;
- 命中 = |p̂ - p*| ≤ tol,tol ∈ {0,1,2};
- 集成权重由 <i 的窗口表现(EWMA)在线决定:先预测,后更新,无前视;
- 同时输出均匀随机基线的理论命中率作为参照下限。
"""
import json
import logging
import time

import numpy as np

from app import config
from app.data.features import build_features
from app.models.baselines import ConstantModel, DampedTrendModel
from app.models.econometric import EtsModel
from app.models.classifier import PeakClassifier
from app.models.ensemble import (
    MemberTracker,
    combine_paths,
    earliest_argmax,
)
from app.models.ml import DirectMultiStepML

log = logging.getLogger(__name__)

TOLS = (0, 1, 2)


def _make_models() -> list:
    models = [
        ConstantModel(),
        DampedTrendModel(),
        EtsModel(),
        DirectMultiStepML("ridge"),
        PeakClassifier(),
    ]
    if config.ENABLE_HGB:
        models.append(DirectMultiStepML("hgb"))
    return models


def _uniform_random_expected(counts: np.ndarray, N: int) -> dict[int, float]:
    """均匀随机猜测的期望命中率:∑_d freq(d)·(窗口内距 d ≤tol 的天数)/N。"""
    freq = counts / counts.sum()
    out = {}
    for tol in TOLS:
        out[tol] = float(
            sum(
                freq[d]
                * (min(N - 1, d + tol) - max(0, d - tol) + 1)
                / N
                for d in range(N)
            )
        )
    return out


def run_backtest(df, oil_df=None, sentiment_df=None, rate_df=None) -> dict:
    """df:store.load_rates() 的结果(升序)。返回完整回测报告。

    oil_df/sentiment_df/rate_df 可选:传递后 build_features 会加入 Brent 油价、
    新闻情绪、关键利率特征,否则这些列全为 NaN 导致 valid 为空,ML 成员
    (M3R/M4)退化为常数模型。
    """
    lp = np.log(df["cny_rub"].to_numpy(dtype=float))
    dates = df.index
    m = len(lp)
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(dtype=float)

    models = _make_models()
    names = [mo.name for mo in models]
    horizons: dict[str, dict] = {}
    t0 = time.time()

    for N in config.N_HORIZONS:
        first_feature = int(valid[0]) if len(valid) else 0
        first = max(config.MIN_TRAIN, first_feature)
        last = m - 1 - N  # 需要 i+N ≤ m-1 才有真实窗口
        if first > last:
            raise RuntimeError(f"数据不足:行数 {m},无法回测 N={N}")
        starts = list(range(first, last + 1, config.REFIT_STRIDE))
        tracker = MemberTracker(names)
        hits: dict[int, dict[str, int]] = {t: {n: 0 for n in names} for t in TOLS}
        ens_hits: dict[int, int] = {t: 0 for t in TOLS}
        yearly: dict[int, dict] = {}
        p_star_counts = np.zeros(N, dtype=int)

        for idx, i in enumerate(starts):
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid}
            paths = {mo.name: np.asarray(mo.predict(ctx, N), dtype=float)
                     for mo in models}
            weights = tracker.weights()
            p_hat = {nm: earliest_argmax(paths[nm]) for nm in names}
            p_hat["ENS-集成"] = earliest_argmax(combine_paths(paths, weights))
            win = lp[i + 1 : i + 1 + N]
            p_star = earliest_argmax(win)
            p_star_counts[p_star] += 1
            yr = int(dates[i].year)
            row = yearly.setdefault(
                yr, {"year": yr, "windows": 0, "hits": {nm: 0 for nm in names},
                     "ens_hits": 0}
            )
            row["windows"] += 1
            for nm, ph in p_hat.items():
                for tol in TOLS:
                    if abs(ph - p_star) <= tol:
                        if nm == "ENS-集成":
                            ens_hits[tol] += 1
                            if tol == 1:
                                row["ens_hits"] += 1
                        else:
                            hits[tol][nm] += 1
                            if tol == 1:
                                row["hits"][nm] += 1
            # 更新成员 EWMA(用 tol=1 的命中),不更新集成
            for mo in models:
                tracker.update(mo.name, abs(p_hat[mo.name] - p_star) <= 1)
            if (idx + 1) % 200 == 0:
                log.info("  N=%d 已评估 %d/%d 窗口 (%.0fs)", N, idx + 1,
                         len(starts), time.time() - t0)

        n_win = len(starts)
        rand = _uniform_random_expected(p_star_counts, N)
        tol_rows: dict[str, dict[str, float]] = {}
        for tol in TOLS:
            tol_rows[str(tol)] = {
                nm: hits[tol][nm] / n_win for nm in names
            }
            tol_rows[str(tol)]["ENS-集成"] = ens_hits[tol] / n_win
            tol_rows[str(tol)]["RAND-均匀随机"] = rand[tol]
        yearly_out = [
            {
                "year": y["year"],
                "windows": y["windows"],
                "rate_tol1": {nm: y["hits"][nm] / y["windows"] for nm in names}
                | {"ENS-集成": y["ens_hits"] / y["windows"]},
            }
            for y in yearly.values()
        ]
        horizons[str(N)] = {
            "N": N,
            "windows": n_win,
            "first_start": dates[starts[0]].isoformat(),
            "last_start": dates[starts[-1]].isoformat(),
            "tol": tol_rows,
            "member_final_hit": tracker.hit_rates(),
            "final_weights": tracker.weights(),
            "yearly": yearly_out,
            "p_star_edge": {
                "first_day": int(p_star_counts[0]),
                "last_day": int(p_star_counts[-1]),
                "share_first_day": round(float(p_star_counts[0] / n_win), 4),
            },
        }
        log.info("N=%d 完成:%d 窗口 (%.0fs)", N, n_win, time.time() - t0)

    return {
        "meta": {
            "as_of": dates[-1].isoformat(),
            "rows": m,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "params": {
                "min_train": config.MIN_TRAIN,
                "train_window": config.TRAIN_WINDOW,
                "refit_stride": config.REFIT_STRIDE,
                "ewma_decay": config.EWMA_DECAY,
                "ens_power": config.ENS_POWER,
                "enable_hgb": config.ENABLE_HGB,
            },
        },
        "horizons": horizons,
    }


def save_report(report: dict) -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    with open(config.BACKTEST_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


def load_report() -> dict | None:
    if not config.BACKTEST_JSON.exists():
        return None
    with open(config.BACKTEST_JSON, encoding="utf-8") as f:
        return json.load(f)
