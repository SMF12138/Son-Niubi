"""新闻情绪数据:从 Google News RSS 抓取俄罗斯/卢布相关新闻,词典法打分。

Google News RSS 完全免费,无需注册或API key。
每天抓取一次,存储到 SQLite。
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

log = logging.getLogger(__name__)

# Google News RSS 搜索关键词(英文,覆盖俄罗斯/卢布/油价/制裁)
_QUERIES = [
    "Russia+ruble+economy",
    "Russia+sanctions+economy",
    "Russia+oil+Brent",
    "Russia+central+bank+interest+rate",
    "Russia+trade+China",
]

# 金融/地缘政治情绪词典(扩展版)
_POSITIVE_WORDS = {
    # 经济增长
    "agreement", "deal", "growth", "stable", "strong", "cooperation", "summit",
    "boost", "surge", "rally", "gain", "rise", "recovery", "improve", "positive",
    "support", "reform", "progress", "investment", "expand", "increase", "peace",
    "ceasefire", "negotiation", "dialogue", "partnership", "export", "revenue",
    "strengthen", "appreciate", "upbeat", "optimistic", "breakthrough",
    "profit", "boom", "rebound", "upturn", "bullish", "outperform",
    # 汇率/货币
    "ruble", "rouble", "appreciation", "hardening", "stabilize", "surplus",
    "reserves", "intervention", "liquidity", "easing",
    # 贸易/合作
    "pipeline", "contract", "supply", "shipment", "import", "bilateral",
    "corridor", "logistics", "pipeline", "grain", "fertilizer",
    # 制裁缓和
    "exemption", "waiver", "relief", "unfreeze", "lift", "ease",
}
_NEGATIVE_WORDS = {
    # 制裁/冲突
    "sanction", "sanctions", "war", "crisis", "conflict", "ban", "restrict",
    "restrictions", "tariff", "crash", "collapse", "plunge", "slump", "decline",
    "fall", "drop", "loss", "risk", "threat", "attack", "escalation", "tension",
    "inflation", "recession", "default", "downgrade", "penalty", "embargo",
    "blockade", "strike", "invasion", "occupation", "retaliation", "deteriorate",
    "weaken", "depreciate", "pessimistic", "bearish", "volatile", "uncertainty",
    "debt", "deficit", "shortage", "curfew", "protest", "unrest",
    # 汇率/货币
    "devaluation", "depreciation", "capitalflight", "capitalcontrols",
    "outflow", "conversion", "freeze", "seize", "confiscate",
    # 地缘
    "drone", "missile", "bombing", "frontline", "mobilization", "conscription",
    "ceasefire", "ultimatum", "provocation", "reprisal",
    # 经济下行
    "default", "insolvency", "bankruptcy", "downgrade", "junk", "contagion",
    "shutdown", "shortage", "hoarding", "rationing",
}

_REQ = requests.Session()


def _score_text(text: str) -> float:
    """词频加权打分:正负词频差 / 总词数,范围 [-1, 1]。用词频而非去重集合。"""
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg = sum(1 for w in words if w in _NEGATIVE_WORDS)
    return (pos - neg) / len(words)


def _parse_rss_date(date_str: str) -> str:
    """解析 RSS 日期格式为 YYYY-MM-DD。"""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip()[:25], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return datetime.now().strftime("%Y-%m-%d")


def fetch_news() -> dict:
    """从 Google News RSS 抓取新闻并打分,写入 SQLite。返回统计。"""
    from app.data import store

    store.init_db()
    rows = []  # (date, title, sentiment, source_query)

    for query in _QUERIES:
        url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
        try:
            resp = _REQ.get(url, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                pub_date = item.findtext("pubDate", "")
                if title and pub_date:
                    d = _parse_rss_date(pub_date)
                    score = _score_text(title)
                    rows.append((d, title[:200], score, query))
        except Exception as e:
            log.warning("抓取新闻失败(%s): %s", query, e)

    if not rows:
        return {"new_rows": 0, "queries": len(_QUERIES), "error": "无新闻数据"}

    store.upsert_news(rows)

    # 统计
    dates = set(r[0] for r in rows)
    avg_score = sum(r[2] for r in rows) / len(rows)
    return {
        "new_rows": len(rows),
        "dates_covered": len(dates),
        "avg_sentiment": round(avg_score, 3),
        "queries": len(_QUERIES),
    }
