"""数据层单测:解析、存储、交易日历、特征因果性。"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from app.data import store
from app.data.calendar import future_trading_dates
from app.data.features import build_features
from app.data.fetcher import _parse_cbr_dynamic

SAMPLE_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ValCurs ID="R01375" DateRange1="01.09.2026" DateRange2="09.09.2026">'
    '<Record Date="01.09.2026"><Nominal>1</Nominal><Value>12,8580</Value></Record>'
    '<Record Date="05.09.2026"><Nominal>1</Nominal><Value>12,8849</Value></Record>'
    "<Record Date='09.09.2026'><Nominal>1</Nominal><Value>12,8842</Value></Record>"
    "</ValCurs>"
).encode("ascii")


class TestCbrParse(unittest.TestCase):
    def test_parse(self):
        out = _parse_cbr_dynamic(SAMPLE_XML)
        self.assertEqual(
            out,
            [("2026-09-01", 12.858), ("2026-09-05", 12.8849),
             ("2026-09-09", 12.8842)],
        )


class TestStore(unittest.TestCase):
    def setUp(self):
        self._db = config.DB_PATH
        self.tmp = Path(tempfile.mkdtemp())
        config.DB_PATH = self.tmp / "rates.db"

    def tearDown(self):
        config.DB_PATH = self._db

    def test_roundtrip_and_upsert(self):
        store.init_db()
        store.upsert_rates([
            ("2026-09-01", 12.8, 90.0, "cbr"),
            ("2026-09-02", 12.9, None, "cbr"),
        ])
        store.upsert_rates([("2026-09-01", 12.85, 91.0, "cbr")])  # 同日更新
        df = store.load_rates()
        self.assertEqual(len(df), 2)
        self.assertEqual(df.loc["2026-09-01", "cny_rub"], 12.85)
        self.assertTrue(np.isnan(df.loc["2026-09-02", "usd_rub"]))
        self.assertEqual(store.last_date(), "2026-09-02")


class TestCalendar(unittest.TestCase):
    def test_skip_weekend_and_holidays(self):
        d = dt.date(2026, 1, 1)  # 俄罗斯新年假期
        dates = future_trading_dates(d, 8)
        self.assertEqual(len(dates), 8)
        self.assertTrue(all(x.weekday() < 5 for x in dates))
        self.assertNotIn(dt.date(2026, 1, 1), dates)
        self.assertEqual(dates[0], dt.date(2026, 1, 9))  # 假期后的首个工作日


class TestKeyRateFeature(unittest.TestCase):
    """H2 回归: key_rate 只能 ffill, 不得用未来决议的利率回填(bfill 前视)。"""

    def test_no_future_rate_leak(self):
        idx = pd.bdate_range("2026-01-01", periods=30)
        df = pd.DataFrame({"cny_rub": np.linspace(12.0, 13.0, len(idx)),
                           "usd_rub": np.linspace(90.0, 91.0, len(idx))}, index=idx)
        # 首个利率日 1/15=10%, 1/22 加息到 20%
        rate_df = pd.DataFrame(
            {"rate": [10.0, 20.0]},
            index=pd.to_datetime(["2026-01-15", "2026-01-22"]))
        F = build_features(df, rate_df=rate_df)
        # 首个利率日之前: NaN(不能被 1/15 的利率回填)
        self.assertTrue(F["key_rate"].loc[:"2026-01-14"].isna().all())
        # 决议日之间沿用旧利率, 1/22 之前不得出现 20%
        between = F["key_rate"].loc["2026-01-15":"2026-01-21"]
        self.assertTrue((between == 10.0).all())
        # 1/22 起才是新利率
        self.assertTrue((F["key_rate"].loc["2026-01-22":] == 20.0).all())


class TestFeatures(unittest.TestCase):
    def test_causal_and_bounds(self):
        rng = np.random.default_rng(1)
        idx = pd.bdate_range("2019-01-01", periods=600)
        cny = 10 + np.cumsum(rng.normal(0, 0.01, len(idx))) + \
            np.sin(np.arange(len(idx)) / 20) * 0.3
        usd = 70 + np.cumsum(rng.normal(0, 0.01, len(idx)))
        df = pd.DataFrame({"cny_rub": cny, "usd_rub": usd}, index=idx)
        brent = 80 + np.cumsum(rng.normal(0, 0.02, len(idx)))
        oil_df = pd.DataFrame({"brent_close": brent}, index=idx)
        F = build_features(df, oil_df=oil_df)
        self.assertEqual(len(F), len(idx))
        # 前 60 行因滚动窗口不足应为 NaN
        self.assertFalse(F.iloc[:60].notna().all().all())
        self.assertTrue(F.iloc[60:].notna().all().all())
        pos = F["pos60"].iloc[60:]
        self.assertTrue(((pos >= 0) & (pos <= 1)).all())


if __name__ == "__main__":
    unittest.main()
