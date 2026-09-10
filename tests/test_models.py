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

    def test_confirm_numbering(self):
        """确认信号编号连续(1-6)。"""
        import inspect
        src = inspect.getsource(MoexDirectionPredictor.predict_direction)
        for n in range(1, 7):
            self.assertIn(f"确认{n}", src, f"缺少确认{n}")


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


if __name__ == "__main__":
    unittest.main()
