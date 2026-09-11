"""MOEX 在岸 CNY/RUB 最新分钟成交价 —— 只用于桌面宠物显示"当前市场价"。

与 moex_rates.py 的分工:
    moex_rates.py  抓**日线**喂模型(dev 信号), 参与预测;
    moex_live.py   只取**当天最后一根分钟线**, 仅供显示, 不参与任何模型计算。

抓取方式与 moex_rates.py 一致(同一个 ISS 端点、同样的 UA 与超时),
只把 interval 从 24(日线) 换成 10(10 分钟线): 10 分钟线一天约 84 根,
一页(100 根)即可取完, 不必翻页; 对"显示当前价"这个用途足够, 最坏延迟 10 分钟。
"""
import datetime as dt
import json
import logging

from app import config
from app.data import http

log = logging.getLogger(__name__)

_BASE = ("https://iss.moex.com/iss/engines/currency/markets/selt/boards/"
         "CETS/securities/CNYRUB_TOM/candles.json")

_LOOKBACK_DAYS = 7      # 今天没成交(闭市/周末/长假)时, 向前找最近一个有数据的交易日
_err_logged = False     # 连续失败只记一次日志: 快层每 60s 跑一次, 否则日志会被刷爆


def fetch_latest(day: dt.date | None = None) -> dict | None:
    """取最近一个有成交的交易日的最后一根 10 分钟 K 线收盘价。

    返回 {"date": "YYYY-MM-DD", "time": "HH:MM", "price": float}。
    任何网络/解析问题一律返回 None(不抛异常), 由调用方回退到官方牌价。
    """
    global _err_logged
    today = day or dt.date.today()
    for back in range(_LOOKBACK_DAYS):
        d = (today - dt.timedelta(days=back)).isoformat()
        url = f"{_BASE}?from={d}&till={d}&interval=10&iss.meta=off&start=0"
        try:
            payload = json.loads(http.open_url(url, timeout=8).read())
        except Exception as e:      # noqa: BLE001
            if not _err_logged:
                log.warning("MOEX 分钟线抓取失败(后续连续失败不再重复记录): %s", e)
                _err_logged = True
            return None
        block = payload.get("candles") or {}
        rows = block.get("data") or []
        if not rows:
            continue    # 这一天没有成交(闭市/假日): 往前一天再试
        cols = {c: i for i, c in enumerate(block.get("columns") or [])}
        if "close" not in cols or "begin" not in cols:
            return None
        for r in reversed(rows):    # 倒着找第一根有收盘价的
            if r[cols["close"]] is not None:
                begin = str(r[cols["begin"]])
                _err_logged = False
                return {"date": begin[:10], "time": begin[11:16],
                        "price": float(r[cols["close"]])}
    return None


def write_live(path=None) -> dict | None:
    """抓一次并原子写入 data/moex_live.json; 失败不写文件(保留上一次的值)。"""
    from app.data import store

    got = fetch_latest()
    if got:
        got["fetched_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store.write_json_atomic(path or config.MOEX_LIVE_JSON, got)
    return got
