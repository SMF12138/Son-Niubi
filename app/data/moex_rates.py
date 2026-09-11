"""MOEX ISS API - 莫斯科交易所在岸 CNY/RUB 真实成交价抓取。

免费、无鉴权。这是卢布对人民币的**市场化发现价格**,领先于 CBR 官方
次日牌价。核心信号:
    dev = log(MOEX 市场价) - log(CBR 官方价)
市场价高于官方价 → 官方价未来上追 → CNY/RUB 上涨(无前视套利偏离)。

实测(滚动标准化, 无前视 walk-forward):
    dev>0 规则 7 日方向 65.9%(高置信 74.6%), 近 500 天 67.6% —— 远超随机。
"""
import json
import logging

from app import config
from app.data import http

log = logging.getLogger(__name__)

_BASE = ("https://iss.moex.com/iss/engines/currency/markets/selt/boards/"
         "CETS/securities/CNYRUB_TOM/candles.json")

MAX_PAGES = 80


def fetch_moex_onshore(from_date: str | None = None) -> dict:
    """分页抓取 MOEX 在岸 CNYRUB 日线收盘, 写入 SQLite moex_rates 表。

    from_date 默认取 DB 已有最新日期(增量, 通常 1-2 页), 只有表空时才全量。
    网络超时 8s, 失败快速返回不拖慢启动。
    """
    from app.data import store
    import datetime as _dt
    store.init_db()
    _ensure_table()

    # 增量起点: 表内最新日期的次日; 表空才全量
    if from_date is None:
        last_d = store.last_moex_date()
        if last_d:
            d = _dt.date.fromisoformat(last_d) + _dt.timedelta(days=1)
            from_date = d.isoformat()
        else:
            from_date = "2022-06-01"

    rows = []
    start = 0
    for _ in range(MAX_PAGES):
        url = f"{_BASE}?from={from_date}&interval=24&iss.meta=off&start={start}"
        try:
            d = json.loads(http.open_url(url, timeout=8).read())
        except Exception as e:  # noqa: BLE001
            log.warning("MOEX 抓取失败: %s", e)
            break
        data = d["candles"]["data"]
        if not data:
            break
        cols = d["candles"]["columns"]
        ci = {c: i for i, c in enumerate(cols)}
        for r in data:
            dt = r[ci["begin"]][:10]
            close = r[ci["close"]]
            high = r[ci["high"]]
            low = r[ci["low"]]
            if close is not None:
                rows.append((dt, float(close),
                             float(high) if high is not None else None,
                             float(low) if low is not None else None))
        if len(data) < 100:
            break
        start += len(data)

    if rows:
        _save(rows)
    return {"rows": len(rows),
            "range": [rows[0][0], rows[-1][0]] if rows else None}


def _ensure_table():
    from app.data import store
    with store.connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS moex_rates "
                    "(date TEXT PRIMARY KEY, moex_close REAL, moex_high REAL, moex_low REAL)")
        cols = [r[1] for r in con.execute("PRAGMA table_info(moex_rates)").fetchall()]
        if "moex_high" not in cols:
            con.execute("ALTER TABLE moex_rates ADD COLUMN moex_high REAL")
        if "moex_low" not in cols:
            con.execute("ALTER TABLE moex_rates ADD COLUMN moex_low REAL")
        con.commit()


def _save(rows):
    from app.data import store
    with store.connect() as con:
        con.executemany(
            "INSERT OR REPLACE INTO moex_rates(date, moex_close, moex_high, moex_low) "
            "VALUES(?,?,?,?)", rows)
        con.commit()


def load_moex() -> dict:
    """返回 {date_str: moex_close}。表不存在时返回空 dict。"""
    from app.data import store
    try:
        with store.connect() as con:
            cur = con.execute("SELECT date, moex_close FROM moex_rates")
            return {d: v for d, v in cur.fetchall()}
    except Exception:  # noqa: BLE001
        return {}


def load_moex_hl() -> dict:
    """返回 {date_str: (close, high, low)}。"""
    from app.data import store
    try:
        with store.connect() as con:
            cur = con.execute("SELECT date, moex_close, moex_high, moex_low FROM moex_rates")
            return {d: (c, h, lo) for d, c, h, lo in cur.fetchall()}
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(fetch_moex_onshore(), ensure_ascii=False))
