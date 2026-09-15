"""CBR(俄罗斯央行)官方牌价抓取 + 降级源。

主源 XML_dynamic 返回逐日 Record,编码 windows-1251,数字为俄式逗号小数,
CNY(ID=R01375)报价方向 = 1 人民币兑多少卢布,与产品口径一致。
"""
import datetime as dt
import logging
import xml.etree.ElementTree as ET

import pandas as pd
import requests

log = logging.getLogger(__name__)

from app import config
from app.data import http
from app.data import store

REQ = requests.Session()


def _record_official_rate(rec: ET.Element) -> float:
    """CBR 官方单一面值牌价 = Value / Nominal。

    CBR 每条 Record 自带 <Nominal>(面值)字段,源头会在零散记录上把面值在
    10/1 之间翻转(2014-12 起直至 2023-01),若只读 Value 会产生 10 倍或
    十分之一的伪牌价(±85%~+893% 假跳变)。Nominal=1 时本除法为恒等,
    对 USD 等无翻转币种无副作用。此处只做官方口径归一化,不做任何异常值
    裁剪、平滑或人工修正。
    """
    value = float(rec.findtext("Value").replace(",", "."))
    nominal = float(rec.findtext("Nominal").replace(",", "."))
    if nominal <= 0:
        raise ValueError(f"CBR Record Nominal 非法: {nominal!r}")
    return value / nominal


def _parse_cbr_dynamic(content: bytes) -> list[tuple[str, float]]:
    """解析 CBR XML_dynamic 响应 -> [(iso_date, 单一面值牌价)]。"""
    root = ET.fromstring(content)  # XML 头声明 windows-1251,ET 按字节自动处理
    out: list[tuple[str, float]] = []
    for rec in root.findall("Record"):
        d = dt.datetime.strptime(rec.get("Date"), "%d.%m.%Y").date().isoformat()
        out.append((d, _record_official_rate(rec)))
    return out


def fetch_dynamic(code: str, d_from: dt.date, d_to: dt.date) -> dict[str, float]:
    """按年分块请求(避免单请求超大),返回 {iso_date: value}。"""
    result: dict[str, float] = {}
    year = d_from.year
    while year <= d_to.year:
        lo = max(d_from, dt.date(year, 1, 1))
        hi = min(d_to, dt.date(year, 12, 31))
        params = {
            "date_req1": lo.strftime("%d/%m/%Y"),
            "date_req2": hi.strftime("%d/%m/%Y"),
            "VAL_NM_RQ": code,
        }
        resp = http.get(config.CBR_DYNAMIC_URL, params=params,
                        timeout=config.HTTP_TIMEOUT, session=REQ)
        resp.raise_for_status()
        if not resp.content.strip().startswith(b"<?xml"):
            raise RuntimeError(f"CBR 返回异常(HTTP {resp.status_code})")
        for d, v in _parse_cbr_dynamic(resp.content):
            result[d] = v
        year += 1
    return result


def fetch_daily_asof() -> dt.date | None:
    """XML_daily 的 Date 属性 = 官方最新牌价所属日期;失败返回 None。"""
    try:
        resp = http.get(config.CBR_DAILY_URL, timeout=config.HTTP_TIMEOUT, session=REQ)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        return dt.datetime.strptime(root.get("Date"), "%d.%m.%Y").date()
    except Exception:
        return None


def fetch_er_api_cny_latest() -> tuple[dt.date, float] | None:
    """降级源:open.er-api.com,免密钥,1 CNY = X RUB(市场中间价)。"""
    try:
        resp = http.get(config.ER_API_LATEST_URL, timeout=config.HTTP_TIMEOUT, session=REQ)
        resp.raise_for_status()
        j = resp.json()
        rub = float(j["rates"]["RUB"])
        d = dt.datetime.strptime(
            j["time_last_update_utc"][5:16], "%d %b %Y"
        ).date()
        return d, rub
    except Exception:
        return None


def sync(progress=None) -> dict:
    """增量同步 CBR 牌价(CNY + USD),返回统计信息。"""
    store.init_db()
    last = store.last_date()
    d_from = (
        dt.date.fromisoformat(last) + dt.timedelta(days=1)
        if last
        else dt.date.fromisoformat(config.DATA_START)
    )
    today = dt.date.today()
    # CBR 每个工作日傍晚发布"次一交易日"的官方牌价(长假前甚至一次发布多日),
    # 记录日期落在未来; 窗口上界放宽到 today+7 天, XML 只返回已发布记录,
    # 查未来空窗无副作用。否则每个工作日傍晚的新牌价要等到次日才能入库。
    d_to = today + dt.timedelta(days=7)
    stats = {"start_from": str(d_from), "cny_rows": 0, "usd_rows": 0,
             "er_api_fill": False, "last_value_date": None}

    if d_from > d_to:
        stats["note"] = "已是最新,无需抓取"
        return stats

    cny = fetch_dynamic(config.CODE_CNY, d_from, d_to)
    usd = fetch_dynamic(config.CODE_USD, d_from, d_to)
    stats["cny_rows"] = len(cny)
    stats["usd_rows"] = len(usd)

    # 按日期并集对齐;CNY 为必有列,USD 缺时留空
    all_dates = sorted(set(cny) | set(usd))
    if not all_dates:
        stats["note"] = "CBR 在区间内无新记录"
        return stats
    df = pd.DataFrame({"cny_rub": cny, "usd_rub": usd}).reindex(all_dates)
    # 个别 CBR 缺失交易日用前一值向前填充并计数
    filled = int(df["cny_rub"].isna().sum())
    df = df.ffill().dropna(subset=["cny_rub"])
    rows = [
        (d, float(r.cny_rub),
         None if pd.isna(r.usd_rub) else float(r.usd_rub), "cbr")
        for d, r in df.iterrows()
    ]
    store.upsert_rates(rows)
    stats["carry_forward_days"] = filled

    # 降级源:若最新记录明显陈旧(>2 自然日),尝试 er-api 补最新日
    db_last = dt.date.fromisoformat(store.last_date())
    stats["last_value_date"] = store.last_date()
    if (today - db_last).days > 2:
        got = fetch_er_api_cny_latest()
        if got:
            d, rub = got
            if d > db_last:
                store.upsert_rates([(d.isoformat(), rub, None, "erapi")])
                stats["er_api_fill"] = True
                stats["last_value_date"] = d.isoformat()
    return stats


def fetch_oil_prices() -> dict:
    """Brent 油价日线,增量写入 SQLite。主源 GitHub CSV + 备用 Yahoo Finance。"""
    import csv
    import io
    import time as _time
    from app.data import store

    store.init_db()
    last = store.last_oil_date()
    stats = {"new_rows": 0, "last_date": None}

    # 主源:GitHub datasets/oil-prices (历史完整, 可能延迟数天)
    try:
        resp = http.get(config.BRENT_CSV_URL, timeout=config.HTTP_TIMEOUT, session=REQ)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = []
        for row in reader:
            d = row["Date"]  # YYYY-MM-DD
            if last and d <= last:
                continue
            try:
                v = float(row["Price"])
            except (ValueError, KeyError):
                continue
            rows.append((d, v))
        store.upsert_oil(rows)
        stats["new_rows"] = len(rows)
        stats["last_date"] = store.last_oil_date()
    except Exception as e:
        log.warning("GitHub 油价主源失败: %s", e)

    # 备用:Yahoo Finance BZ=F (免密钥, 通常当日更新)
    # 主源落后 >3 天才试; 失败(含429)静默降级不刷日志, 避免每次启动拖慢
    current = store.last_oil_date()
    if not current or (dt.date.today() - dt.date.fromisoformat(current)).days > 3:
        try:
            url = ("https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
                   "?range=3mo&interval=1d")
            resp = http.get(url, timeout=5, session=REQ)
            if resp.status_code != 200:
                return stats  # 429/超时: 静默跳过, 不拖慢启动
            resp.raise_for_status()
            j = resp.json()
            ts_list = j["chart"]["result"][0]["timestamp"]
            closes = j["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            rows2 = []
            for tstamp, price in zip(ts_list, closes):
                if price is None:
                    continue
                d = dt.date.fromtimestamp(tstamp).isoformat()
                if last and d <= last:
                    continue
                rows2.append((d, float(price)))
            if rows2:
                store.upsert_oil(rows2)
                stats["new_rows"] += len(rows2)
                stats["last_date"] = store.last_oil_date()
                stats["yahoo_fill"] = True
        except Exception:
            pass  # 备用源失败静默降级

    return stats
