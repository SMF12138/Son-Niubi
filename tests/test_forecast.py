"""期限结构拼接投影测试。

核心场景: 短周期看跌、长周期看涨时, 长周期图必须先跌后涨(V形),
而不是被长周期单一方向抹平成一条单调线。
"""
import datetime as dt
import math
import unittest

from app.forecast import (
    _anchor_disp, build_projection, build_term_projection,
)

CUR = 12.5


def _dates(n, start=dt.date(2026, 9, 15)):
    out, d = [], start
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def _dr(prediction, confidence=0.65, neutral=False):
    d = {"prediction": prediction, "confidence": confidence}
    if neutral:
        d["neutral"] = True
    return d


class TestAnchorDisp(unittest.TestCase):
    def test_signs_and_magnitude(self):
        up = _anchor_disp(_dr(1, 0.70))
        down = _anchor_disp(_dr(0, 0.70))
        self.assertGreater(up, 0)
        self.assertLess(down, 0)
        self.assertAlmostEqual(up, -down, places=10)

    def test_weak_neutral_and_none_are_flat(self):
        # <0.55 弱信号、中性、无方向 -> 锚点零位移
        self.assertEqual(_anchor_disp(_dr(1, 0.54)), 0.0)
        self.assertEqual(_anchor_disp({"prediction": None, "neutral": True}), 0.0)
        self.assertEqual(_anchor_disp(None), 0.0)

    def test_confidence_continuous_no_floor(self):
        # 去掉 0.5 下限: 55%~75% 幅度必须随把握度连续增大(旧 bug 是全部相同)
        d59 = _anchor_disp(_dr(0, 0.59), 7)
        d65 = _anchor_disp(_dr(0, 0.65), 7)
        d70 = _anchor_disp(_dr(0, 0.70), 7)
        self.assertLess(abs(d59), abs(d65))
        self.assertLess(abs(d65), abs(d70))

    def test_longer_horizon_larger_disp(self):
        # 同把握度下, 90 日锚点位移必须大于 7 日(否则拼接曲线中途变平)
        d7 = abs(_anchor_disp(_dr(0, 0.70), 7))
        d90 = abs(_anchor_disp(_dr(0, 0.70), 90))
        self.assertGreater(d90, d7 * 2)


class TestSingleProjection(unittest.TestCase):
    def test_monotone_direction(self):
        dates = _dates(30)
        down = build_projection(CUR, _dr(0, 0.70), dates)
        rates = [p["rate"] for p in down]
        self.assertEqual(len(rates), 30)
        # 看跌: 严格单调下行(4位小数下)
        self.assertTrue(all(a >= b for a, b in zip(rates, rates[1:])))
        self.assertLess(rates[-1], CUR)

    def test_no_direction_empty(self):
        self.assertEqual(build_projection(CUR, None, _dates(7)), [])

    def test_neutral_flat_with_bands(self):
        dates = _dates(7)
        fc = build_projection(CUR, {"prediction": None, "neutral": True}, dates)
        self.assertEqual(len(fc), 7)
        for p in fc:
            self.assertEqual(p["rate"], CUR)       # 中位线贴当前价
            self.assertLess(p["low"], CUR)
            self.assertGreater(p["high"], CUR)

    def test_weak_flat(self):
        fc = build_projection(CUR, _dr(1, 0.48), _dates(7))
        self.assertTrue(all(p["rate"] == CUR for p in fc))


class TestTermProjection(unittest.TestCase):
    def test_v_shape_short_down_long_up(self):
        # 7日看跌, 30日看涨 -> 30日图先跌后涨
        dates = _dates(30)
        d7 = _anchor_disp(_dr(0, 0.70))
        d30 = _anchor_disp(_dr(1, 0.70))
        anchors = [(0, 0.0), (7, d7), (30, d30)]
        fc = build_term_projection(CUR, anchors, dates)
        rates = [p["rate"] for p in fc]
        self.assertEqual(len(rates), 30)
        # 第7天锚点必须低于当前价
        self.assertLess(rates[6], CUR)
        # 末日必须高于当前价
        self.assertGreater(rates[-1], CUR)
        # 前段下行、后段回升: 最低点出现在第7天附近而非末端
        trough_i = min(range(len(rates)), key=lambda i: rates[i])
        self.assertLessEqual(trough_i, 8)

    def test_anchor_endpoints_exact(self):
        dates = _dates(60)
        d7 = _anchor_disp(_dr(0, 0.66))
        d30 = _anchor_disp(_dr(1, 0.60))
        d60 = _anchor_disp(_dr(0, 0.70))
        anchors = [(0, 0.0), (7, d7), (30, d30), (60, d60)]
        fc = build_term_projection(CUR, anchors, dates)
        for k, disp in [(7, d7), (30, d30), (60, d60)]:
            self.assertEqual(fc[k - 1]["rate"],
                             round(math.exp(math.log(CUR) + disp), 4))

    def test_weak_short_model_flat_first_segment(self):
        # 7日弱信号(锚点0), 30日看跌: 前7天平, 之后下行
        dates = _dates(30)
        anchors = [(0, 0.0), (7, _anchor_disp(_dr(1, 0.48))),
                   (30, _anchor_disp(_dr(0, 0.70)))]
        fc = build_term_projection(CUR, anchors, dates)
        rates = [p["rate"] for p in fc]
        self.assertEqual(rates[6], CUR)        # 第7天仍在当前价
        self.assertLess(rates[-1], CUR)

    def test_bands_always_valid(self):
        dates = _dates(90)
        anchors = [(0, 0.0), (7, -0.01), (30, 0.005), (60, -0.008), (90, 0.012)]
        fc = build_term_projection(CUR, anchors, dates, daily_vol=0.008)
        for p in fc:
            self.assertLessEqual(p["low"], p["rate"])
            self.assertGreaterEqual(p["high"], p["rate"])
        # 喇叭口: 末日带宽大于首日
        first_w = fc[0]["high"] - fc[0]["low"]
        last_w = fc[-1]["high"] - fc[-1]["low"]
        self.assertGreater(last_w, first_w)


if __name__ == "__main__":
    unittest.main()
