"""预测永久留档(prediction_ledger)契约测试:

- 同日同周期同版本幂等(快层每60秒重跑不得写花)
- 中性/拒答(prediction=NULL)永久留档但不参与命中统计
- 到期回填严格按 N 个交易日后真实价
- review 滚动窗口取"最近 K 次"且命中率正确
- 影子策略 30 日无闸门方向随 z 符号; 影子行只记录不影响生产
全部使用临时 DB, 不碰生产 rates.db。
"""
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from app.data import store
from app.models import prediction_ledger as pl


def _ou_df(n, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.85 * x[t - 1] + rng.normal(0, 0.01)
    return pd.DataFrame({"cny_rub": np.exp(np.log(12.0) + x)}, index=idx)


def _moex_map(df, moex_from=260, sign=1.0):
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    price = df["cny_rub"].to_numpy(float)
    out = {}
    span = len(df) - moex_from
    for k in range(moex_from, len(df)):
        dev = sign * 0.02 * (k - moex_from + 1) / span
        out[dates[k]] = price[k] * np.exp(dev)
    return out


class TestLedger(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = Path(self._tmp.name) / "test_ledger.db"
        self._patch = unittest.mock.patch.object(config, "DB_PATH", self._db)
        self._patch.start()
        store.init_db()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _drs(self, **over):
        base = {7: {"prediction": 1, "confidence": 0.6, "signal": "moex_dev",
                    "z": 0.4, "confirms": 3},
                30: {"prediction": 1, "confidence": 0.59, "signal": "moex_z"},
                60: {"prediction": 1, "confidence": 0.59, "signal": "pure_rev"},
                90: {"prediction": 1, "confidence": 0.70,
                     "signal": "extreme_dist"}}
        base.update(over)
        return base

    def test_insert_idempotent_same_day(self):
        row = {"as_of": "2026-09-13", "horizon": 7,
               "model_version": config.MODEL_VERSION, "prediction": 1,
               "confidence": 0.6, "meta": "{}"}
        self.assertEqual(store.insert_predictions([row, row]), 1)
        self.assertEqual(store.insert_predictions([dict(row)]), 0)

    def test_neutral_row_kept_but_never_settled(self):
        df1 = _ou_df(520)
        drs = self._drs()
        drs[90] = {"prediction": None, "neutral": True, "reason": "测试中性"}
        pl.record_forecast_day(df1, drs, moex_map=_moex_map(df1))
        full = store.load_ledger(only_realized=False)
        row90 = full[(full["horizon"] == 90) & (full["is_shadow"] == 0)].iloc[0]
        self.assertTrue(pd.isna(row90["prediction"]))
        # 数据延长 100 个交易日后, 中性行仍不得被回填
        df2 = _ou_df(620)
        pl.record_forecast_day(df2, self._drs(), moex_map=_moex_map(df2))
        full = store.load_ledger(only_realized=False)
        row90 = full[(full["horizon"] == 90)
                     & (full["as_of"] == df1.index[-1].date().isoformat())].iloc[0]
        self.assertTrue(pd.isna(row90["realized"]))

    def test_settlement_matches_realized_prices(self):
        df1 = _ou_df(520)
        as_of = df1.index[-1].date().isoformat()
        # 已知方向: 30 看涨, 其余任意; 到期后用真实价核对
        pl.record_forecast_day(df1, self._drs(), moex_map=_moex_map(df1))
        df2 = _ou_df(620)
        pl.record_forecast_day(df2, self._drs(), moex_map=_moex_map(df2))
        lp = np.log(df2["cny_rub"].to_numpy(float))
        dates = [d.strftime("%Y-%m-%d") for d in df2.index]
        pos = {d: i for i, d in enumerate(dates)}
        p0 = pos[as_of]
        led = store.load_ledger(only_realized=True)
        day1 = led[(led["as_of"] == as_of) & (led["is_shadow"] == 0)]
        self.assertEqual(set(day1["horizon"]), {7, 30, 60, 90})  # 全部到期
        for _, r in day1.iterrows():
            n = int(r["horizon"])
            expect = 1 if lp[p0 + n] > lp[p0] else 0
            self.assertEqual(int(r["realized"]), expect)
            self.assertAlmostEqual(r["realized_ret"],
                                   float(lp[p0 + n] - lp[p0]), places=10)
            self.assertEqual(r["realized_date"], dates[p0 + n])

    def test_review_uses_most_recent_k(self):
        # 直接造 25 条已兑现记录: 最近 20 条里 12 次命中 -> hit=0.6
        rows = []
        for k in range(25):
            rows.append({"as_of": f"2025-01-{k + 1:02d}", "horizon": 7,
                         "model_version": "unit-test-v", "prediction": 1,
                         "confidence": 0.6, "meta": "{}"})
        store.insert_predictions(rows)
        full = store.load_ledger(only_realized=False)
        # 最早 5 条全中(不进最近20), 最近 20 条里 12 中 8 错
        order = full.sort_values("as_of")
        for j, (_, r) in enumerate(order.iterrows()):
            hit = 1 if j < 5 or j >= 13 else 0
            store.mark_realized(int(r["id"]), hit, 0.001 * (1 if hit else -1),
                                f"2025-02-{j + 1:02d}")
        rev = pl.review()["models"]["7:unit-test-v"]
        self.assertEqual(rev["windows"]["20"]["n"], 20)
        self.assertAlmostEqual(rev["windows"]["20"]["hit"], 0.6)
        self.assertEqual(rev["windows"]["all"]["n"], 25)
        self.assertAlmostEqual(rev["windows"]["all"]["hit"], 17 / 25)
        self.assertEqual(rev["neutral_days"], 0)

    def test_shadow_30_nogate_follows_z_sign(self):
        # 常数官方价 + MOEX 正偏离线性走阔 -> 末日 z>0, 无闸门影子必看涨
        n = 400
        idx = pd.bdate_range("2022-01-03", periods=n)
        df = pd.DataFrame({"cny_rub": np.full(n, 12.0)}, index=idx)
        rows = pl._shadow_rows(idx[-1].date().isoformat(), df, None,
                               moex_map=_moex_map(df, moex_from=200, sign=1.0))
        nogate = [r for r in rows if r["model_version"] == pl.SHADOW_30_NO_GATE]
        self.assertEqual(len(nogate), 1)
        self.assertEqual(nogate[0]["horizon"], 30)
        self.assertEqual(nogate[0]["prediction"], 1)
        self.assertEqual(nogate[0]["is_shadow"], 1)

    def test_record_twice_same_day_inserts_nothing_second(self):
        df = _ou_df(520)
        r1 = pl.record_forecast_day(df, self._drs(), moex_map=_moex_map(df))
        self.assertGreater(r1["inserted"], 0)
        r2 = pl.record_forecast_day(df, self._drs(), moex_map=_moex_map(df))
        self.assertEqual(r2["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
