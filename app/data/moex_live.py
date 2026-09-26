"""MOEX 在岸 CNY/RUB 最新分钟成交价 —— 只用于桌面宠物显示"当前市场价"。

与 moex_rates.py 的分工:
    moex_rates.py  抓**日线**喂模型(dev 信号), 参与预测;
    moex_live.py   只取**当天最后一根分钟线**, 仅供显示, 不参与任何模型计算。

抓取方式与 moex_rates.py 一致(同一个 ISS 端点、同样的 UA 与超时),
只把 interval 从 24(日线) 换成 1(1 分钟线): 1 分钟线一天约 540 根,
ISS 一页返回全部(实测 223 根在一页内, 无 PAGESIZE 限制);
对"显示当前价"用途足够, 延迟约 1-2 分钟, 比 10 分钟线少 20+ 分钟。
"""
import datetime as dt
import json
import logging
import time

from app import config
from app.data import http

log = logging.getLogger(__name__)

_BASE = ("https://iss.moex.com/iss/engines/currency/markets/selt/boards/"
         "CETS/securities/CNYRUB_TOM/candles.json")

_LOOKBACK_DAYS = 7      # 今天没成交(闭市/周末/长假)时, 向前找最近一个有数据的交易日
_HTTP_TRIES = 3         # 单次 URL 抓取尝试次数(含首次): 偶发 SSL/超时抖动重试两次
_ERR_LOG_INTERVAL = 3600.0   # 同类失败日志节流间隔(秒): 既不刷爆日志, 也不永久静默
_last_err_logged = 0.0  # 上次记录抓取失败的 monotonic 时刻


def _open_with_retry(url: str) -> bytes | None:
    """抓 URL, 网络异常时退避重试(1s/2s), 全败返回 None。

    只重试"请求没发出去/响应没拿到"类异常; HTTP 200 但 K 线为空是闭市的正常
    响应, 不算失败, 由调用方按天回退, 不触发重试。
    """
    global _last_err_logged
    last_exc: Exception | None = None
    for attempt in range(_HTTP_TRIES):
        try:
            return http.open_url(url, timeout=8).read()
        except Exception as e:      # noqa: BLE001
            last_exc = e
            if attempt < _HTTP_TRIES - 1:
                time.sleep(attempt + 1)
    now = time.monotonic()
    if now - _last_err_logged >= _ERR_LOG_INTERVAL:
        log.warning("MOEX 分钟线抓取失败(已重试 %d 次, %.0f 分钟内不再重复记录): %s",
                    _HTTP_TRIES - 1, _ERR_LOG_INTERVAL / 60, last_exc)
        _last_err_logged = now
    return None


def fetch_latest(day: dt.date | None = None) -> dict | None:
    """取最近一个有成交的交易日的最后一根 1 分钟 K 线收盘价。

    返回 {"date": "YYYY-MM-DD", "time": "HH:MM", "price": float}。
    任何网络/解析问题一律返回 None(不抛异常), 由调用方回退到官方牌价。
    """
    today = day or dt.date.today()
    for back in range(_LOOKBACK_DAYS):
        d = (today - dt.timedelta(days=back)).isoformat()
        url = f"{_BASE}?from={d}&till={d}&interval=1&iss.meta=off&start=0"
        raw = _open_with_retry(url)
        if raw is None:
            return None     # 网络层彻底失败(已重试): 不再往前翻天, 等下个 60s 周期
        try:
            payload = json.loads(raw)
        except Exception:      # noqa: BLE001
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
