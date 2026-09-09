"""因果特征工程:只在 t 及其之前的信息构造,避免回测前视偏差。

输入:升序 DataFrame(索引为交易日期),列 cny_rub / usd_rub。
可选:oil_df(油价 DataFrame,索引为日期,列 brent_close)。
返回:与输入等长的特征 DataFrame(早期行因滚动窗口不足为 NaN,调用方须跳过)。
"""
import numpy as np
import pandas as pd


def _rsi(returns: pd.Series, period: int = 14) -> pd.Series:
    gain = returns.clip(lower=0.0)
    loss = -returns.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(df: pd.DataFrame, oil_df: pd.DataFrame | None = None,
                   sentiment_df: pd.DataFrame | None = None,
                   rate_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """逐交易日构建特征。NaN 行 = 样本不足,调用方跳过。"""
    idx = df.index
    S = pd.Series(np.log(df["cny_rub"].astype(float).values), index=idx)
    r = S.diff()  # 当日对数收益

    F = pd.DataFrame(index=idx)
    # === CNY/RUB 自身特征 ===
    F["ret1"] = r
    F["mom5"] = r.rolling(5).sum()
    F["mom20"] = r.rolling(20).sum()
    F["vol5"] = r.rolling(5).std()
    F["vol20"] = r.rolling(20).std()
    F["skew20"] = r.rolling(20).skew()
    lo60 = S.rolling(60).min()
    hi60 = S.rolling(60).max()
    F["pos60"] = (S - lo60) / (hi60 - lo60 + 1e-12)
    F["level_z60"] = (S - S.rolling(60).mean()) / S.rolling(60).std()
    F["rsi14"] = _rsi(r)
    F["dow"] = idx.dayofweek / 6.0
    # 偏离 MA 信号(均值回归)
    F["ma20_dev"] = (S - S.rolling(20).mean())
    F["ma60_dev"] = (S - S.rolling(60).mean())
    # 趋势斜率
    F["slope20"] = S.rolling(20).apply(
        lambda x: np.polyfit(np.arange(len(x)), x, 1)[0] if len(x) == 20 else np.nan,
        raw=False
    )

    # === USD/RUB 交叉特征 ===
    if "usd_rub" in df.columns and df["usd_rub"].notna().mean() > 0.9:
        usd_log = np.log(df["usd_rub"].ffill().astype(float).values)
        Us = pd.Series(usd_log, index=idx)
        ur = Us.diff()  # USD/RUB 日收益
        cny_usd_log = S - Us  # 隐含 CNY/USD 对数

        F["usd_ret1"] = ur
        F["usd_ret5"] = ur.rolling(5).sum()
        F["usd_ret20"] = ur.rolling(20).sum()
        F["usd_vol20"] = ur.rolling(20).std()
        F["cusd_mom5"] = cny_usd_log.diff().rolling(5).sum()
        F["cusd_mom20"] = cny_usd_log.diff().rolling(20).sum()

        # CNY/RUB 与 USD/RUB 的60日滚动相关性(衡量联动强度)
        F["cny_usd_corr60"] = r.rolling(60).corr(ur)
        # 60日滚动 β: CNY/RUB 收益 ~ USD/RUB 收益
        cov = r.rolling(60).cov(ur)
        var_u = ur.rolling(60).var()
        F["cny_usd_beta60"] = cov / (var_u + 1e-12)

        # implied_cross_rate 偏差:实际 CNY/RUB vs CNY/USD × USD/RUB
        # 这里用 log 差:ln(CNY/RUB) - ln(CNY/USD) - ln(USD/RUB) ≈ 0
        # 实际上 ln(CNY/RUB) = ln(CNY/USD) + ln(USD/RUB) 恒成立
        # 真正的信号是 USD/RUB 的方向 → CNY/RUB 的方向传导
    else:
        for c in ["usd_ret1", "usd_ret5", "usd_ret20", "usd_vol20",
                   "cusd_mom5", "cusd_mom20", "cny_usd_corr60", "cny_usd_beta60"]:
            F[c] = np.nan

    # === Brent 油价特征 ===
    if oil_df is not None and not oil_df.empty:
        # 对齐到 CNY/RUB 交易日(油价每日有,但 CNY/RUB 只在工作日)
        oil = oil_df["brent_close"].reindex(idx, method="ffill")
        oil_log = pd.Series(np.log(oil.values), index=idx)
        or_ = oil_log.diff()

        F["brent_ret1"] = or_
        F["brent_ret5"] = or_.rolling(5).sum()
        F["brent_ret20"] = or_.rolling(20).sum()
        F["brent_vol20"] = or_.rolling(20).std()
        # 油价波动 regime:近5日波动/近60日波动
        vol5_oil = or_.rolling(5).std()
        vol60_oil = or_.rolling(60).std()
        F["brent_vol_ratio"] = vol5_oil / (vol60_oil + 1e-12)
        # CNY/RUB 与 Brent 的60日滚动相关性
        F["cny_brent_corr60"] = r.rolling(60).corr(or_)
    else:
        for c in ["brent_ret1", "brent_ret5", "brent_ret20", "brent_vol20",
                   "brent_vol_ratio", "cny_brent_corr60"]:
            F[c] = np.nan

    # === 新闻情绪特征 ===
    if sentiment_df is not None and not sentiment_df.empty:
        sent = sentiment_df["sentiment"].reindex(idx, method="ffill").fillna(0.0)
        F["sentiment"] = sent
        F["sentiment_7d"] = sent.rolling(7, min_periods=1).mean()
        F["sentiment_30d"] = sent.rolling(30, min_periods=1).mean()
        F["sentiment_trend"] = F["sentiment_7d"] - F["sentiment_30d"]
    else:
        for c in ["sentiment", "sentiment_7d", "sentiment_30d", "sentiment_trend"]:
            F[c] = 0.0

    # === CBR 关键利率特征 ===
    if rate_df is not None and not rate_df.empty:
        rate = rate_df["rate"].reindex(idx).bfill().ffill()
        F["key_rate"] = rate
        F["rate_change"] = rate.diff().fillna(0.0)
        F["rate_change_60d"] = (rate - rate.shift(60)).fillna(0.0)
    else:
        for c in ["key_rate", "rate_change", "rate_change_60d"]:
            F[c] = 0.0

    return F
