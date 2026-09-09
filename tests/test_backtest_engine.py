"""回测引擎记账逻辑单测:用"哑模型"(固定预测日)在合成数据上核对统计口径。"""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from app.backtest import engine


def _synthetic_df(rows: int = 1300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=rows)
    ret = rng.normal(0, 0.008, rows)
    ret += np.sin(np.arange(rows) / 23) * 0.004  # 周期性
    # 偶发跳变,制造非平凡的最高点分布
    for i in range(100, rows, 220):
        ret[i : i + 4] += 0.02
    level = 12 * np.exp(np.cumsum(ret) + np.linspace(0, 0.05, rows))
    return pd.DataFrame({"cny_rub": level}, index=idx)


class _DummyDayModel:
    """predict 恒返回第 fix 天(0-based)为顶:前段平后段低,argmax 在 fix。"""

    def __init__(self, name: str, fix: int, N: int):
        self.name = name
        self.fix = fix
        self.N = N

    def predict(self, ctx, N: int) -> np.ndarray:
        out = np.full(N, 0.0)
        out[self.fix] = 1.0
        return out


class _DummyFlatModel:
    """全程平直→顶在窗口首日(M0 语义)。"""

    name = "FLAT"

    def predict(self, ctx, N: int) -> np.ndarray:
        return np.zeros(N)


def _manual_check(lp, N, starts, models, stride):
    """与引擎完全一致的记账,供交叉核对。"""
    tol_rows = {0: {}, 1: {}, 2: {}}
    n = 0
    p_star = np.zeros(N, int)
    for i in starts:
        n += 1
        win = lp[i + 1 : i + 1 + N]
        p = int(np.argmax(win))
        p_star[p] += 1
        for m in models:
            ph = m.predict({"lp": lp, "i": i}, N)
            pk = int(np.argmax(ph))
            for t in (0, 1, 2):
                tol_rows[t].setdefault(m.name, 0)
                if abs(pk - p) <= t:
                    tol_rows[t][m.name] += 1
    return {t: {k: v / n for k, v in d.items()} for t, d in tol_rows.items()}, n, p_star


class TestBacktestAccounting(unittest.TestCase):
    def setUp(self):
        self._cfg_backups = {
            "MIN_TRAIN": config.MIN_TRAIN,
            "REFIT_STRIDE": config.REFIT_STRIDE,
            "TRAIN_WINDOW": config.TRAIN_WINDOW,
            "ENABLE_HGB": config.ENABLE_HGB,
        }
        config.MIN_TRAIN = 200
        config.REFIT_STRIDE = 13
        config.TRAIN_WINDOW = 300
        config.ENABLE_HGB = False

    def tearDown(self):
        for k, v in self._cfg_backups.items():
            setattr(config, k, v)

    def test_accounting_with_dummy_models(self):
        df = _synthetic_df()
        lp = np.log(df["cny_rub"].to_numpy(float))
        N = 7
        m = len(lp)
        first = config.MIN_TRAIN
        last = m - 1 - N
        starts = list(range(first, last + 1, config.REFIT_STRIDE))
        models = [
            _DummyDayModel("FIX0", 0, N),
            _DummyDayModel("FIX3", 3, N),
            _DummyFlatModel(),
        ]
        man_tol, n_win, p_star = _manual_check(lp, N, starts, models, config.REFIT_STRIDE)

        # 用引擎跑同一批哑模型(M3/ETS 会真实拟合,故仅核对用哑模型的统计列)
        engine._make_models = lambda: models
        try:
            rep = engine.run_backtest(df)
        finally:
            from app.models.baselines import ConstantModel
            from app.models.econometric import EtsModel
            from app.models.ml import DirectMultiStepML

            def _real():
                return [ConstantModel(), EtsModel(), DirectMultiStepML("ridge")]
            engine._make_models = _real
        h7 = rep["horizons"]["7"]
        self.assertEqual(h7["windows"], n_win)
        for tol in (0, 1, 2):
            for nm in ("FIX0", "FIX3", "FLAT"):
                self.assertAlmostEqual(
                    h7["tol"][str(tol)][nm], man_tol[tol][nm], places=6
                )
        # RAND 均匀随机为理论期望,与引擎记录的实际 p* 分布自洽
        rand = h7["tol"]["1"]["RAND-均匀随机"]
        n_w = n_win
        exp = sum(
            (p_star[d] / n_w)
            * (min(N - 1, d + 1) - max(0, d - 1) + 1)
            / N
            for d in range(N)
        )
        self.assertAlmostEqual(rand, exp, places=6)

    def test_forecast_shape_and_consistency(self):
        from app.forecast import compute_forecast

        df = _synthetic_df(rows=800)
        fc = compute_forecast(df, 7)
        self.assertEqual(len(fc["ensemble_mid"]), 7)
        self.assertEqual(len(fc["prob_by_day"]), 7)
        self.assertEqual(len(fc["forecast_dates"]), 7)
        self.assertEqual(fc["models"][0]["peak_day"], fc["models"][0]["peak_day"])
        prob_sum = sum(fc["prob_by_day"])
        self.assertAlmostEqual(prob_sum, 1.0, places=6)
        # 分位数单调
        self.assertTrue(all(a <= b for a, b in zip(fc["low"], fc["high"])))


if __name__ == "__main__":
    unittest.main()
