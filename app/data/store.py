"""SQLite 缓存:历史牌价 + 油价 + 新闻情绪 + meta 键值。"""
import sqlite3

import pandas as pd

from app import config


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS rates("
            "date TEXT PRIMARY KEY,"
            "cny_rub REAL NOT NULL,"
            "usd_rub REAL,"
            "source TEXT NOT NULL DEFAULT 'cbr')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS oil_prices("
            "date TEXT PRIMARY KEY,"
            "brent_close REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS news_sentiment("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "date TEXT NOT NULL,"
            "title TEXT,"
            "sentiment REAL,"
            "source TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS key_rate("
            "date TEXT PRIMARY KEY, rate REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS moex_rates("
            "date TEXT PRIMARY KEY,"
            "moex_close REAL, moex_high REAL, moex_low REAL)"
        )
        conn.commit()


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def upsert_rates(rows: list[tuple]) -> None:
    if not rows:
        return
    with connect() as conn:
        conn.executemany(
            "INSERT INTO rates(date,cny_rub,usd_rub,source) VALUES(?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "cny_rub=CASE WHEN excluded.cny_rub IS NOT NULL "
            "THEN excluded.cny_rub ELSE rates.cny_rub END,"
            "usd_rub=CASE WHEN excluded.usd_rub IS NOT NULL "
            "THEN excluded.usd_rub ELSE rates.usd_rub END,"
            "source=CASE WHEN excluded.source='cbr' THEN 'cbr' "
            "ELSE rates.source END",
            rows,
        )
        conn.commit()


def load_rates() -> pd.DataFrame:
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, cny_rub, usd_rub FROM rates ORDER BY date", conn
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["cny_rub"] = df["cny_rub"].astype(float)
    df["usd_rub"] = pd.to_numeric(df["usd_rub"], errors="coerce")
    return df


def last_date() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(date) AS d FROM rates").fetchone()
    return row["d"]


def upsert_oil(rows: list[tuple]) -> None:
    if not rows:
        return
    with connect() as conn:
        conn.executemany(
            "INSERT INTO oil_prices(date,brent_close) VALUES(?,?) "
            "ON CONFLICT(date) DO UPDATE SET brent_close=excluded.brent_close",
            rows,
        )
        conn.commit()


def last_oil_date() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(date) AS d FROM oil_prices").fetchone()
    return row["d"]


def load_oil() -> pd.DataFrame:
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, brent_close FROM oil_prices ORDER BY date", conn
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["brent_close"] = df["brent_close"].astype(float)
    return df


def upsert_news(rows: list[tuple]) -> None:
    """rows: (date, title, sentiment, source_query)。去重:同日同标题同源不重复插入。"""
    if not rows:
        return
    with connect() as conn:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_news_dedup "
            "ON news_sentiment(date, title, source)"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO news_sentiment(date,title,sentiment,source) "
            "VALUES(?,?,?,?)",
            rows,
        )
        conn.commit()


def load_daily_sentiment() -> pd.DataFrame:
    """返回按日聚合的情绪均值,索引为 date,列 sentiment/n_articles。"""
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, AVG(sentiment) as sentiment, COUNT(*) as n_articles "
            "FROM news_sentiment GROUP BY date ORDER BY date", conn
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def upsert_key_rate(rows: list[tuple]) -> None:
    """rows: (date, rate)。"""
    if not rows:
        return
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS key_rate("
            "date TEXT PRIMARY KEY, rate REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO key_rate(date,rate) VALUES(?,?) "
            "ON CONFLICT(date) DO UPDATE SET rate=excluded.rate",
            rows,
        )
        conn.commit()


def load_key_rate() -> pd.DataFrame:
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, rate FROM key_rate ORDER BY date", conn
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df
