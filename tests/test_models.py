"""模型层单测:校准单调性、均值回复预测、MOEX 偏离预测、投影函数。"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from app import config
from app.models.meanrev_dir import MeanRevDirectionPredictor
from app.models.moex_dir import (
    MoexDirectionPredictor,
    _DEFAULT_CAL,
    _DEFAULT_CAP,
    _load_calibration,
)
from app.forecast import build_projection


class TestCalibrationMonotonicity(unittest.TestCase):
    """T11: 校准值单调(z00 <= z05 <= z10 <= z15)。

    注意契约已变: calibrate_moex_z **不再无条件强制单调**。相邻档非单调时, 仅当两档差异
    统计上不可区分(两比例检验 |z|<1.96)才做样本量加权池化; 差异真实则如实保留非单调。
    所以这里校验的是"当前数据下确实单调"。**若将来失败, 先判断该反转是否统计显著**
    (显著 = "偏离越大越准"的模型假设失效, 需人工决断), 不要直接改回强制单调。
    """

    def test_default_cal_monotonic(self):
        for N, tbl in _DEFAULT_CAL.items():
            z00, z05, z10, z15 = tbl["z00"], tbl["z05"], tbl["z10"], tbl["z15"]
            self.assertLessEqual(z00, z05, f"N={N}: z00 > z05")
            self.assertLessEqual(z05, z10, f"N={N}: z05 > z10")
            self.assertLessEqual(z10, z15, f"N={N}: z10 > z15")

    def test_default_cap_is_per_bucket_dict(self):
        for N, tbl in _DEFAULT_CAP.items():
            self.assertIsInstance(tbl, dict, f"N={N}: cap should be dict")
            self.assertEqual(set(tbl.keys()), {"z15", "z10", "z05", "z00"})

    def test_default_cap_monotonic(self):
        for N, tbl in _DEFAULT_CAP.items():
            z00, z05, z10, z15 = tbl["z00"], tbl["z05"], tbl["z10"], tbl["z15"]
            self.assertLessEqual(z00, z05, f"N={N}: cap z00 > z05")
            self.assertLessEqual(z05, z10, f"N={N}: cap z05 > z10")
            self.assertLessEqual(z10, z15, f"N={N}: cap z10 > z15")

    def test_calibration_json_monotonic(self):
        cal_path = config.DATA_DIR / "calibration.json"
        if not cal_path.exists():
            self.skipTest("calibration.json 不存在")
        with open(cal_path, encoding="utf-8") as f:
            data = json.load(f)
        for N_str, tbl in data.get("cal", {}).items():
            z00, z05, z10, z15 = tbl["z00"], tbl["z05"], tbl["z10"], tbl["z15"]
            self.assertLessEqual(z00, z05, f"N={N_str}: z00({z00}) > z05({z05})")
            self.assertLessEqual(z05, z10, f"N={N_str}: z05({z05}) > z10({z10})")
            self.assertLessEqual(z10, z15, f"N={N_str}: z10({z10}) > z15({z15})")

    def test_calibration_json_cap_is_per_bucket(self):
        cal_path = config.DATA_DIR / "calibration.json"
        if not cal_path.exists():
            self.skipTest("calibration.json 不存在")
        with open(cal_path, encoding="utf-8") as f:
            data = json.load(f)
        for N_str, cap_tbl in data.get("cap", {}).items():
            self.assertIsInstance(cap_tbl, dict, f"N={N_str}: cap should be dict")
            self.assertEqual(set(cap_tbl.keys()), {"z15", "z10", "z05", "z00"})


class TestMeanRevPredictor(unittest.TestCase):
    """T12: MeanRevDirectionPredictor 基本行为。"""

    def _make_ctx(self, n=600, rng_seed=42):
        rng = np.random.default_rng(rng_seed)
        idx = pd.bdate_range("2019-01-01", periods=n)
        cny = 10 + np.cumsum(rng.normal(0, 0.01, n))
        usd = 70 + np.cumsum(rng.normal(0, 0.01, n))
        df = pd.DataFrame({"cny_rub": cny, "usd_rub": usd}, index=idx)
        from app.data.features import build_features
        Fdf = build_features(df)
        valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
        Xf = Fdf.to_numpy(float)
        feat_names = list(Fdf.columns)
        lp = np.log(df["cny_rub"].to_numpy(float))
        return {"lp": lp, "i": len(lp) - 1, "Xf": Xf, "valid": valid,
                "feat_names": feat_names}

    def test_flat_when_insufficient_data(self):
        pred = MeanRevDirectionPredictor()
        ctx = {"lp": np.ones(10), "i": 5, "Xf": np.ones((10, 3)),
               "valid": np.array([5]), "feat_names": ["a", "b", "c"]}
        r = pred.predict_direction(ctx, 7)
        self.assertEqual(r["confidence"], 0.5)

    def test_calibration_loading(self):
        pred = MeanRevDirectionPredictor()
        pred.set_calibration({7: 0.68, 30: 0.72})
        self.assertEqual(pred._meanrev_conf[7], 0.68)
        self.assertEqual(pred._meanrev_conf[30], 0.72)


class TestMoexPredictor(unittest.TestCase):
    """T13: MoexDirectionPredictor 基本结构。"""

    def test_init_loads_calibration(self):
        pred = MoexDirectionPredictor()
        self.assertIsNotNone(pred._cal)
        self.assertIsNotNone(pred._cap)

    def test_init_with_calibration_file(self):
        tmp = Path(tempfile.mkdtemp())
        cal_path = tmp / "calibration.json"
        cap_bucket = {"z15": 0.80, "z10": 0.75, "z05": 0.68, "z00": 0.60}
        cal_path.write_text(json.dumps({
            "generated": "2026-09-10T12:00:00",
            "generated_ts": time.time(),
            "cal": {"7": {"z15": 0.80, "z10": 0.75, "z05": 0.68, "z00": 0.60}},
            "cap": {"7": cap_bucket},
            "meanrev": {"7": 0.70},
        }), encoding="utf-8")
        with patch("app.models.moex_dir._CALIBRATION_PATH", cal_path):
            pred = MoexDirectionPredictor()
        self.assertEqual(pred._cal[7]["z15"], 0.80)
        self.assertEqual(pred._cap[7], cap_bucket)
        self.assertEqual(pred._meanrev_conf.get(7), 0.70)

    def test_confirms_behavioral_alignment(self):
        """六重确认闸门行为测试(替代旧的"源码里搜中文字符串"伪测试):

        手工构造对齐数组与特征矩阵: z>0、MOEX 动量向上、偏离加深、高波动、
        油价跌、情绪正面、连续偏离 -> confirms 应 >=4 且看涨;
        全部反向 -> 看跌。
        """
        pred = MoexDirectionPredictor()
        n_moex = 65
        # 前 63 日微弱正偏离, 最近 2 日显著扩大 -> 最近两日 z 为正且连续
        devs = np.full(n_moex, 0.001)
        devs[-2:] = [0.02, 0.03]
        rows = np.arange(100, 100 + n_moex)          # 官方价行号 100..164
        lp = np.full(165, np.log(12.0))
        i = int(rows[-1])
        closes = np.full(n_moex, 12.0)
        closes[-6:] = [12.0, 12.05, 12.1, 12.2, 12.3, 12.4]  # 5 日动量向上
        hlr = np.full(n_moex, 0.01)
        hlr[-1] = 0.05                               # 当日高波动
        for arr_name, arr in [("_h_idx", rows), ("_h_dev", devs),
                              ("_h_close", closes), ("_h_hlr", hlr)]:
            setattr(pred, arr_name, arr)
        pred._h_pos = {int(j): t for t, j in enumerate(rows)}
        z = np.full(n_moex, np.nan)
        z[-2:] = [2.0, 3.0]
        pred._h_z = z
        pred._h_hlr_med = np.full(n_moex, 0.01)
        feat_names = ["brent_ret20", "sentiment_7d"]
        Xf = np.zeros((165, 2))
        Xf[i, 0] = -0.05    # 油价跌 -> 对 CNY/RUB 看涨, 与 z 同向
        Xf[i, 1] = 0.5      # 情绪正面 -> 看涨
        ctx = {"lp": lp, "i": i, "Xf": Xf,
               "valid": np.array([i]), "feat_names": feat_names}
        r = pred.predict_direction(ctx, 7)
        self.assertEqual(r["signal"], "moex_dev")
        self.assertEqual(r["prediction"], 1)
        self.assertGreaterEqual(r["confirms"], 4)
        self.assertFalse(r["weak_signal"])

        # 全部反向: z<0 + 油价涨 + 情绪负面
        devs2 = np.full(n_moex, -0.001)
        devs2[-2:] = [-0.02, -0.03]
        pred._h_dev = devs2
        closes2 = np.full(n_moex, 12.0)
        closes2[-6:] = [12.4, 12.3, 12.2, 12.1, 12.05, 12.0]
        pred._h_close = closes2
        pred._h_z = np.where(np.isnan(z), np.nan, -np.abs(z))
        Xf[i, 0] = 0.05
        Xf[i, 1] = -0.5
        r2 = pred.predict_direction(ctx, 7)
        self.assertEqual(r2["prediction"], 0)
        self.assertGreaterEqual(r2["confirms"], 4)

    def test_weak_signal_flag(self):
        """|z| 极小时必须打 weak_signal, 供界面标注"方向中性"。"""
        pred = MoexDirectionPredictor()
        n_moex = 65
        devs = np.full(n_moex, 0.01)                # 完全恒定 -> z≈0
        rows = np.arange(100, 100 + n_moex)
        lp = np.full(165, np.log(12.0))
        i = int(rows[-1])
        for arr_name, arr in [("_h_idx", rows), ("_h_dev", devs),
                              ("_h_close", np.full(n_moex, 12.0)),
                              ("_h_hlr", np.full(n_moex, np.nan))]:
            setattr(pred, arr_name, arr)
        pred._h_pos = {int(j): t for t, j in enumerate(rows)}
        pred._h_z = np.zeros(n_moex)
        pred._h_hlr_med = np.full(n_moex, np.nan)
        ctx = {"lp": lp, "i": i, "Xf": np.zeros((165, 1)),
               "valid": np.array([i]), "feat_names": ["other"]}
        r = pred.predict_direction(ctx, 7)
        self.assertEqual(r["signal"], "moex_dev")
        self.assertTrue(r["weak_signal"])


class TestBacktestSmoke(unittest.TestCase):
    """端到端冒烟: 在"MOEX 偏离机械领先官方价"的合成序列上, 回测必须显著
    跑赢多数类基线; 同时锁住 OOS 校准的元数据口径。防止无前视管线被改坏。"""

    def test_predictable_series_beats_baseline(self):
        from app.models.moex_dir import run_direction_backtest
        rng = np.random.default_rng(7)
        n = 500
        idx = pd.bdate_range("2022-01-03", periods=n)
        # 偏离以 30 日为块持续正负; 次日官方收益 = 0.8*当日偏离 + 微噪声
        block = np.where(np.arange(n) // 30 % 2 == 0, 1.0, -1.0)
        dev = 0.015 * block + rng.normal(0, 0.0005, n)
        lp = np.empty(n)
        lp[0] = np.log(12.0)
        for t in range(n - 1):
            lp[t + 1] = lp[t] + 0.8 * dev[t] + rng.normal(0, 0.0005)
        moex_map = {d.strftime("%Y-%m-%d"): float(np.exp(lp[t] + dev[t]))
                    for t, d in enumerate(idx)}
        df = pd.DataFrame({
            "cny_rub": np.exp(lp),
            "usd_rub": 90.0 + np.cumsum(rng.normal(0, 0.05, n)),
        }, index=idx)
        with patch("app.data.moex_rates.load_moex", return_value=moex_map), \
                patch("app.data.moex_rates.load_moex_hl", return_value={}):
            rep = run_direction_backtest(df)
        self.assertEqual(rep["meta"]["calibration"], "expanding_window_oos")
        h7 = rep["horizons"]["7"]
        self.assertGreater(h7["accuracy"], 0.80)
        self.assertGreater(h7["moex_accuracy"], 0.80)
        self.assertGreater(h7["accuracy"], h7["baseline_always_majority"] + 0.15)
        self.assertGreater(h7["moex_windows"], 100)


class TestBuildProjection(unittest.TestCase):
    """T14: build_projection 一致性。"""

    def test_basic_projection(self):
        dates = [f"2026-09-{d:02d}" for d in range(11, 18)]
        direction = {"prediction": 1, "confidence": 0.70}
        result = build_projection(12.85, direction, dates)
        self.assertEqual(len(result), 7)
        # 看涨: 最后一天 rate > 第一天 rate
        self.assertGreater(result[-1]["rate"], result[0]["rate"])
        # 有 uncertainty band
        for pt in result:
            self.assertIn("low", pt)
            self.assertIn("high", pt)
            self.assertLessEqual(pt["low"], pt["rate"])
            self.assertGreaterEqual(pt["high"], pt["rate"])

    def test_down_direction(self):
        dates = [f"2026-09-{d:02d}" for d in range(11, 18)]
        direction = {"prediction": 0, "confidence": 0.70}
        result = build_projection(12.85, direction, dates)
        # 看跌: 最后一天 rate < 第一天 rate
        self.assertLess(result[-1]["rate"], result[0]["rate"])

    def test_empty_direction_returns_empty(self):
        result = build_projection(12.85, None, ["2026-09-11"])
        self.assertEqual(result, [])

    def test_empty_dates_returns_empty(self):
        result = build_projection(12.85, {"prediction": 1, "confidence": 0.7}, [])
        self.assertEqual(result, [])

    def test_band_scales_with_realized_vol(self):
        dates = [f"2026-09-{d:02d}" for d in range(11, 18)]
        direction = {"prediction": 1, "confidence": 0.70}
        vol = 0.01
        result = build_projection(12.85, direction, dates, daily_vol=vol)
        widths = [r["high"] - r["low"] for r in result]
        # 带宽随 sqrt(k) 单调扩大
        for a, b in zip(widths, widths[1:]):
            self.assertGreater(b, a)
        # 末日半宽 ≈ vol*sqrt(7) (对数空间, 容差 15%)
        import math
        half = math.log(result[-1]["high"] / result[-1]["rate"])
        self.assertAlmostEqual(half, vol * math.sqrt(7), delta=vol * math.sqrt(7) * 0.15)

    def test_recent_daily_vol(self):
        from app.forecast import recent_daily_vol
        self.assertIsNone(recent_daily_vol(np.zeros(10)))
        lp = np.log(12.0 + np.cumsum(np.random.default_rng(0).normal(0, 0.1, 100)))
        v = recent_daily_vol(lp)
        self.assertIsNotNone(v)
        self.assertGreater(v, 0)


if __name__ == "__main__":
    unittest.main()
