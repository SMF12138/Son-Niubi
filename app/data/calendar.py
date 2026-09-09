"""交易日历:未来交易日的近似推演(回测不依赖本模块,仅用于预测日期标签)。

口径:俄罗斯央行牌价只在工作日发布。历史窗口的"交易日"直接用实际有数据的日期序列,
无需预测日历。此处仅把未来 N 个观测行映射到自然日期:跳过周末与俄法定固定假日。
俄政府对节假日的调休(把周末挪成工作日)不建模,属已知近似,已在 README 说明。
"""
import datetime as dt

# 俄法定固定假日(月, 日)。新年 1/1-1/8 + 2/23 + 3/8 + 5/1 + 5/9 + 6/12 + 11/4。
_FIXED_HOLIDAYS = [
    (1, d) for d in range(1, 9)
] + [(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)]
_HOLIDAY_SET = set(_FIXED_HOLIDAYS)


def future_trading_dates(start: dt.date, n: int) -> list[dt.date]:
    """从 start 次日起,返回接下来 n 个近似交易日(自然日期)。"""
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        d = d + dt.timedelta(days=1)
        if d.weekday() < 5 and (d.month, d.day) not in _HOLIDAY_SET:
            out.append(d)
    return out
