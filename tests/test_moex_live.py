"""moex_live 抓取健壮性: 闭市按天回退 / 网络抖动重试 / 全败返回 None。"""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import moex_live as ml


class _Resp:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self):
        return self._raw


CANDLES_EMPTY = b'{"candles":{"columns":["begin","close"],"data":[]}}'
CANDLES_OK = (b'{"candles":{"columns":["begin","close"],'
              b'"data":[["2026-09-25 17:19:00",12.5935]]}}')

SATURDAY = dt.date(2026, 9, 26)


class FetchLatestTest(unittest.TestCase):
    def test_closed_day_falls_back_to_previous_trading_day(self):
        # 周六空 K 线(HTTP 成功, 非网络失败, 不重试) -> 回退到周五拿到数据
        with mock.patch.object(ml.http, "open_url",
                               side_effect=[_Resp(CANDLES_EMPTY),
                                            _Resp(CANDLES_OK)]) as m:
            got = ml.fetch_latest(day=SATURDAY)
        self.assertEqual(got, {"date": "2026-09-25", "time": "17:19",
                               "price": 12.5935})
        self.assertEqual(m.call_count, 2)

    def test_retries_transient_failure_then_succeeds(self):
        calls = {"n": 0}

        def fake_open(url, timeout, headers=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("simulated transient timeout")
            return _Resp(CANDLES_OK)

        with mock.patch.object(ml.http, "open_url", side_effect=fake_open), \
                mock.patch.object(ml.time, "sleep"):
            got = ml.fetch_latest(day=SATURDAY)
        self.assertIsNotNone(got)
        self.assertEqual(got["price"], 12.5935)
        self.assertEqual(calls["n"], 3)

    def test_all_attempts_fail_returns_none(self):
        with mock.patch.object(ml.http, "open_url",
                               side_effect=ConnectionError("down")), \
                mock.patch.object(ml.time, "sleep"):
            self.assertIsNone(ml.fetch_latest(day=SATURDAY))

    def test_retry_sleeps_with_backoff(self):
        sleeps = []
        with mock.patch.object(ml.http, "open_url",
                               side_effect=TimeoutError("x")), \
                mock.patch.object(ml.time, "sleep", side_effect=sleeps.append):
            ml.fetch_latest(day=SATURDAY)
        self.assertEqual(sleeps, [1, 2])


if __name__ == "__main__":
    unittest.main()
