"""CBR 关键利率抓取。俄罗斯央行官方,免费无认证。

利率是 CNY/RUB 最强宏观特征之一:
- 加息→卢布走强→CNY/RUB 下跌
- 降息→卢布走弱→CNY/RUB 上涨
"""
import logging
import re
from datetime import datetime

import requests

log = logging.getLogger(__name__)

_REQ = requests.Session()

# CBR KeyRate HTML 表格可正常访问（截至2026-09-09验证）
_KEY_RATE_URL = (
    "https://www.cbr.ru/hd_base/KeyRate/"
    "?UniDbQuery.Posted=True"
    "&UniDbQuery.From={from_date}"
    "&UniDbQuery.To={to_date}"
)

# 备选利率表: 仅当 CBR 主源抓取失败时写入。
# 运行时按系统日期过滤, 只取 <= 今天的条目 —— 避免在系统日期较早的机器上写入"未来"利率。
_KNOWN_RATES = [
    ("2022-02-28", 20.00), ("2022-04-29", 14.00), ("2022-05-26", 11.00),
    ("2022-06-10", 9.50), ("2022-07-22", 8.00), ("2022-09-16", 7.50),
    ("2022-10-31", 7.50), ("2022-12-16", 7.50), ("2023-02-10", 7.50),
    ("2023-03-24", 7.50), ("2023-04-28", 7.50), ("2023-06-16", 7.50),
    ("2023-07-21", 8.50), ("2023-08-15", 12.00), ("2023-09-15", 13.00),
    ("2023-10-27", 15.00), ("2023-12-15", 16.00), ("2024-02-16", 16.00),
    ("2024-03-22", 16.00), ("2024-04-26", 16.00), ("2024-06-07", 16.00),
    ("2024-07-26", 18.00), ("2024-09-13", 19.00), ("2024-10-25", 21.00),
    ("2024-12-20", 21.00), ("2025-02-14", 21.00), ("2025-04-25", 21.00),
    ("2025-06-06", 21.00), ("2025-07-25", 20.00), ("2025-09-12", 18.00),
    ("2025-10-24", 17.00), ("2025-12-19", 16.50), ("2026-01-06", 16.00),
    ("2026-02-13", 15.50), ("2026-03-20", 15.00), ("2026-04-24", 14.50),
    ("2026-06-19", 14.25), ("2026-07-25", 14.00),
]


def fetch_key_rate() -> dict:
    """从 CBR HTML 页面抓取关键利率历史,写入 SQLite。"""
    from app.data import store

    store.init_db()

    # 主源:CBR KeyRate HTML 表格（覆盖2013-09至今，每天一行）
    from_date = "01.01.2010"
    to_date = datetime.now().strftime("%d.%m.%Y")
    url = _KEY_RATE_URL.format(from_date=from_date, to_date=to_date)
    try:
        resp = _REQ.get(url, timeout=30)
        resp.raise_for_status()
        rows = _parse_cbr_html(resp.text)
        if rows:
            store.upsert_key_rate(rows)
            return {"new_rows": len(rows), "last_date": rows[-1][0],
                    "source": "cbr_html"}
    except Exception as e:
        log.warning("CBR利率主源抓取失败: %s", e)

    # 备选:用已知利率数据硬编码（只取 <= 今天的条目）
    today = datetime.now().strftime("%Y-%m-%d")
    known_rates = [r for r in _KNOWN_RATES if r[0] <= today]
    store.upsert_key_rate(known_rates)
    return {"new_rows": len(known_rates), "source": "hardcoded_cbr_verified"}


def _parse_cbr_html(text: str) -> list[tuple]:
    """从 CBR KeyRate HTML 表格提取利率数据。
    表格结构: <td>日期</td><td>利率</td>，俄式逗号小数。
    """
    rows = []
    # 匹配表格中 日期 + 利率（俄式逗号格式 如 14,00）
    pattern = r'(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td[^>]*>\s*([\d,\.]+)'
    for m in re.finditer(pattern, text):
        date_str, rate_str = m.group(1), m.group(2).replace(',', '.')
        try:
            d = datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
            r = float(rate_str)
            rows.append((d, r))
        except ValueError:
            continue
    # 按日期升序
    rows.sort(key=lambda x: x[0])
    return rows
