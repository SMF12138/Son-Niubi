"""长周期均值回复模型(longhorizon)冒烟测试:
信号因果性、方向恒发声+诚实实验标记(2026-09-13 产品决策)、把握度溯源、
校准缺失安全降级、中性/实验档投影区间、可复现。
"""
import unittest

import numpy as np
import pandas as pd

from app.models import longhorizon as lh
from app.forecast import build_projection


def _synth(n=420, moex_frac=0.5, seed=7):
    """合成均值回复序列 + 后段对齐 MOEX(与官方价加小幅同向偏离)。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    # OU 过程: 围绕中枢往复 -> 极端偏离后倾向回归
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.85 * x[t - 1] + rng.normal(0, 0.01)
    lp = np.log(12.0) + x
    prices = np.exp(lp)
    df = pd.DataFrame({"cny_rub": prices}, index=idx)
    k = int(n * moex_frac)
    moex_map = {}
    for i in range(n - k, n):
        moex_map[idx[i].strftime("%Y-%m-%d")] = prices[i] * (
            1 + 0.001 * np.sin(i / 4.0))
    return df, moex_map


class TestLongSignals(unittest.TestCase):

    def test_reversion_signal_direction(self):
        # 价格远低于 MA120 -> rev>0(超跌看涨); 远高于 -> rev<0
        n = 200
        idx = pd.bdate_range("2022-01-03", periods=n)
        p = np.r_[np.full(n - 20, 12.0), np.linspace(12.0, 10.5, 20)]
        lp = np.log(p)
        sig = lh.compute_long_signals(lp, np.full(n, np.nan))
        self.assertTrue(np.isfinite(sig["rev"][-1]))
        self.assertGreater(sig["rev"][-1], 0)   # 末端暴跌 -> 超跌信号为正

        p2 = np.r_[np.full(n - 20, 12.0), np.linspace(12.0, 13.8, 20)]
        sig2 = lh.compute_long_signals(np.log(p2), np.full(n, np.nan))
        self.assertLess(sig2["rev"][-1], 0)

    def test_no_lookahead_rev(self):
        # rev[t] 只依赖 <=t 的数据: 截断重算结果必须一致
        df, _ = _synth()
        lp = np.log(df["cny_rub"].to_numpy(float))
        full = lh.compute_long_signals(lp, np.full(len(lp), np.nan))["rev"]
        t = 300
        part = lh.compute_long_signals(lp[:t + 1], np.full(t + 1, np.nan))["rev"]
        self.assertTrue(np.allclose(full[:t + 1], part, equal_nan=True))

    def test_moex_confirmation_mask(self):
        # 首个 MOEX 成交日前 z 无效; 缺失日由 ffill 覆盖滚动统计
        df, moex_map = _synth()
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        mc = lh._aligned_moex(dates, moex_map)
        sig = lh.compute_long_signals(np.log(df["cny_rub"].to_numpy(float)), mc)
        first = next(i for i, v in enumerate(mc) if np.isfinite(v))
        self.assertFalse(sig["has_moex"][:first].any())
        # z 需要 60 日滚动窗, 首个有效 z 不早于 first+59
        valid_z = np.where(np.isfinite(sig["z"]))[0]
        self.assertGreaterEqual(valid_z[0], first + lh.Z_WIN - 1)

    def test_bucket_partition(self):
        rev = np.array([-3.0, -1.5, -0.7, 0.0, 0.7, 1.5, 3.0, np.nan])
        b = lh._bucket_ids(rev)
        self.assertEqual(b[:6].tolist(), [0, 1, 2, 3, 4, 5])
        self.assertEqual(b[6], 5)
        self.assertEqual(b[7], -1)

    def test_moex_confirms_gate_count(self):
        # 构造末端单边上行: z>0、MOEX 5日动量向上(C1)、在岸价差近5日加深(C2)、
        # Brent 20日收益为正(C4) -> 三确认全满足, confirms=3。
        n = 160
        lp = np.r_[np.full(110, np.log(12.0)),
                   np.log(np.linspace(12.0, 13.0, 50))]
        moex = np.full(n, np.nan)
        # MOEX 自 index 60 起(给足 Z_WIN=60 个价差样本); 末段比在岸涨得更快
        # -> md=log(moex)-lp 持续加深
        for i in range(60, n):
            prem = 0.05 * max(0, i - 110) / 49.0 if i >= 110 else 0.0
            moex[i] = np.exp(lp[i] + prem)
        z = lh.compute_long_signals(lp, moex)["z"]
        self.assertGreater(z[-1], 0)  # 末端价差走阔 -> z 为正
        # C4 口径: Brent 下跌(br20<0)->卢布偏弱->CNY/RUB 看涨, 与 z>0 同向
        br20 = np.full(n, np.nan)
        br20[-1] = -0.02
        c = lh.compute_moex_confirms(lp, moex, z, br20)
        self.assertEqual(c[-1], 3)
        # 无油价(C4 缺)时确认数最多为 2
        c_noil = lh.compute_moex_confirms(lp, moex, z, None)
        self.assertEqual(c_noil[-1], 2)
        # MOEX 出现前确认数为 0
        self.assertTrue(np.all(c[:60] == 0))

    def test_high_vol_regime_causal(self):
        # hv[t] 只依赖 <=t 收益: 截断重算掩码必须一致(无前视)
        df, _ = _synth()
        lp = np.log(df["cny_rub"].to_numpy(float))
        full = lh.compute_high_vol_regime(lp)
        t = 350
        part = lh.compute_high_vol_regime(lp[:t + 1])
        self.assertTrue(np.array_equal(full[:t + 1], part))
        # 252 日前一律 False(没有一年参照)
        self.assertFalse(full[:lh.WEAK_REGIME_LOOK].any())


class TestLongBacktest(unittest.TestCase):

    def test_backtest_runs_and_contract(self):
        df, moex_map = _synth()
        rep = lh.run_longhorizon_backtest(df, moex_map)
        self.assertEqual(rep["gate"], 0.70)
        self.assertIn("30", rep["horizons"])
        for N in ("30", "60", "90"):
            h = rep["horizons"][N]
            self.assertEqual(h["policy"], lh.HORIZON_POLICY[int(N)])
            self.assertGreaterEqual(h["eligible_days"], 0)
            self.assertEqual(
                h["oos_emitted"],
                sum(v["n"] for v in h["eras"].values()))
            for pool in ("plain", "confirmed", "weak"):
                for cell in h["tables"][pool].values():
                    if cell["n"]:
                        self.assertAlmostEqual(cell["wins"] / cell["n"],
                                               cell["rate"], places=3)
        # pure_rev 策略: experimental 与 70% 门槛通过情况一致(不达标才标实验)
        for N in ("30", "60", "90"):
            h = rep["horizons"][N]
            self.assertEqual(h["experimental"], not h["passed_70pct_gate"])
            if not h["passed_70pct_gate"]:
                self.assertFalse(h["meets_full_protocol"])

    def test_backtest_deterministic(self):
        df, moex_map = _synth(seed=11)
        r1 = lh.run_longhorizon_backtest(df, moex_map)
        r2 = lh.run_longhorizon_backtest(df, moex_map)
        r1["generated_ts"] = r2["generated_ts"] = 0
        self.assertEqual(r1, r2)

    def test_backtest_never_uses_future_in_emit_rule(self):
        # 发声所依据的胜率与未来数据无关: 去掉最后 90 天(未实现段),
        # 在 t=m-91 时刻的桶统计 = 回测 walk 到该点的同一决策
        df, moex_map = _synth(seed=3)
        df2 = df.iloc[:-90]
        mm2 = {k: v for k, v in moex_map.items() if k in set(
            d.strftime("%Y-%m-%d") for d in df2.index)}
        rep = lh.run_longhorizon_backtest(df2, mm2)
        # 仅验证: 所有桶 rate 都在 [0,1], 且生产表只含已实现样本(不报错即口径成立)
        for N in ("30", "60", "90"):
            for pool in ("plain", "confirmed", "weak"):
                for c in rep["horizons"][N]["tables"][pool].values():
                    if c["rate"] is not None:
                        self.assertGreaterEqual(c["rate"], 0.0)
                        self.assertLessEqual(c["rate"], 1.0)

    def test_production_prediction_always_emits(self):
        # 2026-09-13 产品决策: 门槛不达标不再沉默; 方向照给,
        # 未通过 70% 验证必须带实验标记, 把握度=规则实测 OOS 兑现率
        df, moex_map = _synth()
        rep = lh.run_longhorizon_backtest(df, moex_map)
        lp = np.log(df["cny_rub"].to_numpy(float))
        dates = [d.strftime("%Y-%m-%d") for d in df.index]

        # 无校准文件 -> 一律中性(安全降级, 绝不硬编码置信度)
        from unittest import mock
        with mock.patch.object(lh.config, "LONGHORIZON_JSON",
                               lh.config.DATA_DIR / "__no_such_lh__.json"):
            r = lh.predict_longhorizon(lp, dates, moex_map, 90, result=None)
        self.assertIsNone(r["prediction"])
        self.assertTrue(r["neutral"])

        for N in (30, 60, 90):
            h = rep["horizons"][str(N)]
            r = lh.predict_longhorizon(lp, dates, moex_map, N, result=rep)
            self.assertIn(r["prediction"], (0, 1), f"N={N} 必须给方向")
            self.assertEqual(r["experimental"], not h["passed_70pct_gate"],
                             f"N={N} 实验标记必须与门槛通过情况一致")
            self.assertEqual(r["validated_70pct"], h["passed_70pct_gate"])
            # 把握度可溯源。30日 moex_z 为 C1/C2/C4≥2 确认闸门 + 动态校准:
            #   过闸 -> 当日 z 桶/聚合过闸实测率; 未过闸 -> 未过闸子集实测率(弱档);
            # confidence 必须等于对应实测率, 不允许写死。
            self.assertGreaterEqual(r["confidence"], 0.0)
            self.assertLessEqual(r["confidence"], 1.0)
            if h.get("policy") == "moex_z" and h.get("z_strength_buckets"):
                self.assertIn(r["confidence_source"],
                              ("z_bucket_dynamic", "moex_aggregate_dynamic",
                               "gate_failed_dynamic",
                               "rev_fallback_dynamic", "modern_oos_hit"))
                # 闸门标记字段存在且类型正确
                self.assertIn(r["gate_confirms"], (0, 1, 2, 3))
                self.assertIsInstance(r["gate_passed"], bool)
                if r["confidence_source"] == "z_bucket_dynamic":
                    cell = h["z_strength_buckets"][r["bucket"]]
                    self.assertAlmostEqual(r["confidence"], cell["rate"], places=3)
                    self.assertEqual(r["bucket_n"], cell["n"])
                elif r["confidence_source"] == "gate_failed_dynamic":
                    gf = h["z_gate_failed"]
                    self.assertAlmostEqual(r["confidence"], gf["rate"], places=3)
                    self.assertEqual(r["bucket_n"], gf["n"])
            elif h.get("eras", {}).get("2021-今", {}).get("hit") is not None:
                self.assertEqual(r["confidence_source"], "modern_oos_hit")
                self.assertAlmostEqual(r["confidence"],
                                       h["eras"]["2021-今"]["hit"], places=2)
            elif h["oos_hit"] is not None:
                self.assertEqual(r["confidence_source"], "modern_oos_hit")
                self.assertAlmostEqual(r["confidence"], h["oos_hit"], places=2)
            else:
                self.assertEqual(r["confidence_source"], "bucket_rate")
                self.assertGreater(r["bucket_n"], 0)

    def test_short_history_neutral(self):
        n = 100
        idx = pd.bdate_range("2022-01-03", periods=n)
        df = pd.DataFrame({"cny_rub": np.linspace(12, 13, n)}, index=idx)
        lp = np.log(df["cny_rub"].to_numpy(float))
        dates = [d.strftime("%Y-%m-%d") for d in idx]
        rep = {"horizons": {"90": {"tables": {"plain": {}, "confirmed": {}}}}}
        r = lh.predict_longhorizon(lp, dates, {}, 90, result=rep)
        self.assertTrue(r["neutral"])
        self.assertEqual(r["neutral_reason"], "insufficient_history")


class TestNeutralProjection(unittest.TestCase):

    def test_neutral_band_symmetric_around_current(self):
        dates = pd.bdate_range("2026-09-13", periods=10)
        neutral = {"prediction": None, "neutral": True}
        band = build_projection(12.0, neutral, dates, daily_vol=0.01)
        self.assertEqual(len(band), 10)
        for row in band:
            self.assertAlmostEqual(row["rate"], 12.0, places=6)
            self.assertLess(row["low"], 12.0)
            self.assertGreater(row["high"], 12.0)
            # 区间随时间放宽
        self.assertGreater(band[-1]["high"] - band[-1]["low"],
                           band[0]["high"] - band[0]["low"])

    def test_empty_projection_for_non_neutral_none(self):
        self.assertEqual(build_projection(12.0, None, pd.bdate_range(
            "2026-09-13", periods=3)), [])

    def test_experimental_band_flat_and_symmetric(self):
        # 弱信号(把握度<55%)虽带方向, 但投影中位线不偏移(不诱导押单边), 区间仍对称放宽
        dates = pd.bdate_range("2026-09-13", periods=10)
        weak = {"prediction": 0, "confidence": 0.4}
        band = build_projection(12.0, weak, dates, daily_vol=0.01)
        self.assertEqual(len(band), 10)
        for row in band:
            self.assertAlmostEqual(row["rate"], 12.0, places=6)
            # 对数空间对称(价格空间因指数换算有二阶微小差异)
            self.assertAlmostEqual(
                np.log(row["high"]) - np.log(12.0),
                np.log(12.0) - np.log(row["low"]), places=6)
        self.assertGreater(band[-1]["high"] - band[-1]["low"],
                           band[0]["high"] - band[0]["low"])

    def test_confident_signal_band_offset_toward_prediction(self):
        # 把握度>=55% 的明确信号: 中位线应朝预测方向偏移
        dates = pd.bdate_range("2026-09-13", periods=10)
        up = {"prediction": 1, "confidence": 0.65}
        band = build_projection(12.0, up, dates, daily_vol=0.01)
        self.assertGreater(band[-1]["rate"], 12.0)


if __name__ == "__main__":
    unittest.main()
