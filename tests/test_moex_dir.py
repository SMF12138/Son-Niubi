"""7日 MOEX 价差模型(MoexDirectionPredictor)核心契约测试:

- 滚动 z 无前视(截断重算一致)
- 方向严格跟随 z 符号; 无 MOEX 日落 meanrev 兜底
- 把握度 = 所属 |z| 桶(动态)校准率: 六确认只展示, 永不加分(2026-09 下线假接线)
- OOS 桶表样本不足 20 回退内置默认值
- 新闻情绪对回测结果零影响(2026-09 已从生产链路下线, 此处为回归保护)
"""
import unittest

import numpy as np
import pandas as pd

from app.data.features import build_features
from app.models.moex_dir import (
    MoexDirectionPredictor, _bucket_key, _oos_bucket_table,
    _Z_BUCKETS, _DEFAULT_CAL, run_direction_backtest,
)


def _synth_ramp(n=300, sign=1.0, moex_from=150):
    """常数官方价 + MOEX 偏离线性走阔到 ±2%: 末端产生大 |z|(落 z15),
    且 C1(动量同向)/C2(偏离加深)/C6(连续同向) 三确认成立。"""
    idx = pd.bdate_range("2022-01-03", periods=n)
    price = np.full(n, 12.0)
    dates = [d.strftime("%Y-%m-%d") for d in idx]
    moex_map = {}
    for k in range(moex_from, n):
        dev = sign * 0.02 * (k - moex_from + 1) / (n - moex_from)
        moex_map[dates[k]] = price[k] * np.exp(dev)
    return idx, np.log(price), dates, moex_map


def _ctx(lp, i):
    df = pd.DataFrame({"cny_rub": np.exp(lp)},
                      index=pd.bdate_range("2022-01-03", periods=len(lp)))
    Fdf = build_features(df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    return {"lp": lp, "i": i, "Xf": Fdf.to_numpy(float),
            "valid": valid, "feat_names": list(Fdf.columns)}, df


class TestMoexDirection(unittest.TestCase):

    def test_bucket_assignment_boundaries(self):
        # 严格大于阈值才进档: z00=[0,.5), z05=[.5,1), z10=[1,1.5), z15=(1.5,inf)
        self.assertEqual(_bucket_key(0.0), "z00")
        self.assertEqual(_bucket_key(0.49), "z00")
        self.assertEqual(_bucket_key(0.51), "z05")
        self.assertEqual(_bucket_key(1.01), "z10")
        self.assertEqual(_bucket_key(1.51), "z15")
        self.assertEqual(_bucket_key(5.0), "z15")

    def test_z_is_causal_no_lookahead(self):
        # attach 全量与截断到 T 的前半段, 共同 MOEX 日上的滚动 z 必须逐位相等
        idx, lp, dates, moex_map = _synth_ramp()
        p_full = MoexDirectionPredictor()
        p_full.attach_moex(dates, lp, moex_map)
        T = 250
        p_part = MoexDirectionPredictor()
        p_part.attach_moex(dates[:T], lp[:T],
                           {d: v for d, v in moex_map.items() if d in dates[:T]})
        self.assertTrue(np.allclose(p_full._h_z[:T - 150],
                                    p_part._h_z, equal_nan=True))

    def test_direction_follows_z_and_confirms_fire(self):
        for sign, want in ((1.0, 1), (-1.0, 0)):
            idx, lp, dates, moex_map = _synth_ramp(sign=sign)
            pred = MoexDirectionPredictor()
            pred.attach_moex(dates, lp, moex_map)
            ctx, _ = _ctx(lp, 290)
            r = pred.predict_direction(ctx, 7,
                                       cal_tbl={bk: 0.7 for bk, _ in _Z_BUCKETS},
                                       cap_tbl={bk: 0.85 for bk, _ in _Z_BUCKETS})
            self.assertEqual(r["signal"], "moex_dev")
            self.assertEqual(r["prediction"], want)
            self.assertEqual(r["z_bucket"], "z15")
            # 线性走阔: 动量同向 + 偏离加深 + 连续同向, 三确认必成立
            self.assertGreaterEqual(r["confirms"], 3)
            self.assertLessEqual(r["confirms"], 6)

    def test_confidence_never_bonused_by_confirms(self):
        # 关键契约: 即使 confirms>=3 且 cap 留有余量, 把握度也必须严格等于
        # 该桶校准率(2026-09 实测确认增量不稳定, +1/+2% 加分已下线)
        for sign in (1.0, -1.0):
            _, lp, _, moex_map = _synth_ramp(sign=sign)
            dates = pd.bdate_range("2022-01-03", periods=len(lp)).strftime(
                "%Y-%m-%d").tolist()
            pred = MoexDirectionPredictor()
            pred.attach_moex(dates, lp, moex_map)
            ctx, _ = _ctx(lp, 290)
            r = pred.predict_direction(ctx, 7,
                                       cal_tbl={bk: 0.7 for bk, _ in _Z_BUCKETS},
                                       cap_tbl={bk: 0.85 for bk, _ in _Z_BUCKETS})
            self.assertGreaterEqual(r["confirms"], 3)
            self.assertEqual(r["confidence"], 0.7)

    def test_fallback_meanrev_without_moex(self):
        # 前 150 日无 MOEX -> 必走均值回复兜底, 不返回 moex_dev
        _, lp, dates, moex_map = _synth_ramp(moex_from=150)
        pred = MoexDirectionPredictor()
        pred.attach_moex(dates, lp, moex_map)
        ctx, _ = _ctx(lp, 50)
        r = pred.predict_direction(ctx, 7)
        self.assertEqual(r["signal"], "mean_rev")
        self.assertIn(r["prediction"], (0, 1))

    def test_oos_bucket_table_falls_back_below_min_n(self):
        hits = {bk: 0 for bk, _ in _Z_BUCKETS}
        tots = {bk: 0 for bk, _ in _Z_BUCKETS}
        tbl = _oos_bucket_table(7, hits, tots)
        self.assertEqual(tbl, _DEFAULT_CAL[7])  # 全空 -> 内置默认表
        # 某桶 19 个(全对)仍回退; 20 个全对 -> 采用实测 1.0
        tots["z00"] = 19
        self.assertEqual(_oos_bucket_table(7, hits, tots)["z00"],
                         _DEFAULT_CAL[7]["z00"])
        tots["z00"] = 20
        self.assertEqual(_oos_bucket_table(
            7, {**hits, "z00": 20}, tots)["z00"], 1.0)

    def test_sentiment_has_zero_effect_on_backtest(self):
        # 回归保护: 极端新闻情绪不得改变任何周期的方向/把握度统计
        rng = np.random.default_rng(7)
        n = 420
        idx = pd.bdate_range("2022-01-03", periods=n)
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = 0.85 * x[t - 1] + rng.normal(0, 0.01)
        prices = np.exp(np.log(12.0) + x)
        df = pd.DataFrame({"cny_rub": prices}, index=idx)
        sent = pd.DataFrame({"sentiment": np.full(n, 0.8)}, index=idx)
        base = run_direction_backtest(df)
        with_sent = run_direction_backtest(df, sentiment_df=sent)
        for N in ("7", "30", "60", "90"):
            a, b = base["horizons"][N], with_sent["horizons"][N]
            for key in ("accuracy", "confident_accuracy", "confident_windows",
                        "moex_accuracy", "moex_windows"):
                self.assertEqual(a[key], b[key], f"N={N} {key} 受情绪影响")


if __name__ == "__main__":
    unittest.main()
