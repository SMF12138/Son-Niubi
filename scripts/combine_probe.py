"""无 MOEX 窗口上的每日组合信号探针(产品相关性最高)。

对比在【无 MOEX 的窗口】上:
1. 当前 fallback = meanrev_dir 合成均值回复(MREV_FEATS 5特征)
2. 把它扩展成 每日可用组合信号(MREV + usd/cusd/brent/利率)的加权线性合成
两者谁的未来 N 天方向准确率更高。

结论决定: 是否值得把每日信号组合真实接入 meanrev_dir fallback。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from app.data import store
from app.data.features import build_features
from app.models.mean_reversion import synthetic_meanrev_score
from app.models.moex_dir import MoexDirectionPredictor

MREV_FEATS = ["ma60_dev", "ma20_dev", "mom20", "pos60", "cusd_mom20"]


def main():
    store.init_db()
    df = store.load_rates()
    oil = store.load_oil()
    sent = store.load_daily_sentiment()
    rate = store.load_key_rate()
    lp = np.log(df["cny_rub"].to_numpy(float))
    Fdf = build_features(df, oil_df=oil, sentiment_df=sent, rate_df=rate)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float)
    feat_names = list(Fdf.columns)
    dates = df.index
    m = len(lp)

    # MOEX 起始
    moex_start_i = int(np.searchsorted(dates, "2022-06-01"))

    # 每日可用的补充特征(正负方向对应 future CNY/RUB 涨跌的逻辑方向)
    # 通则: 油价涨→RUB强→CNY/RUB跌; USD/RUB涨→RUB弱→CNY/RUB涨;
    #       隐含CNY/USD(===0.5-only, 因为 CNY/RUB 单边受 USD/RUB 主导)  放组合里让机器权衡
    EXTRA = ["brent_ret20", "brent_ret5", "usd_ret20", "usd_ret5",
             "cusd_mom20", "cusd_mom5", "sentiment_7d", "rate_change",
             "cny_usd_beta60", "cny_brent_corr60", "brent_vol_ratio"]

    for N in [7, 30, 60, 90]:
        last = m - 1 - N
        first = 300
        # 无MOEX窗口收集
        cur_c = cur_t = comb_c = comb_t = 0
        for i in range(first, last + 1):
            if i >= moex_start_i:
                continue  # 只看无MOEX窗口
            pos = np.searchsorted(valid, i)
            if pos >= len(valid) or valid[pos] != i:
                continue
            actual = 1 if lp[i + N] > lp[i] else 0

            # 当前 fallback(均值回复分数)
            cols = [feat_names.index(c) for c in MREV_FEATS if c in feat_names]
            sig = synthetic_meanrev_score(Xf, valid, i, cols)
            cur_pred = 1 if sig["score"] > 0 else 0
            cur_c += int(cur_pred == actual); cur_t += 1

            # 扩展合成: MREV score + 每日组合(标准化各特征符号)
            s = sig["score"]
            ext_cols = [feat_names.index(c) for c in EXTRA if c in feat_names]
            if ext_cols:
                hist = valid[valid <= i][-500:]
                Xi = Xf[hist][:, ext_cols].astype(float)
                mu = np.nanmean(Xi, 0); sd = np.nanstd(Xi, 0) + 1e-9
                xnow = Xf[i, ext_cols]
                z = np.where(sd > 1e-6, (xnow - mu) / sd, 0.0)
                # 每个特征按其逻辑方向转为"signed toward CNY/RUB up"
                signed = 0.0
                # usd/cusd 动量: 涨→(看涨还是看跌由特征本身方向) 这里让 brent 反向, 其余正向
                weights = {"brent_ret20": -1, "brent_ret5": -1,
                           "usd_ret20": 1, "usd_ret5": 1,
                           "cusd_mom20": 1, "cusd_mom5": 1,
                           "sentiment_7d": 1, "rate_change": -1,
                           "cny_usd_beta60": 1, "cny_brent_corr60": -1,
                           "brent_vol_ratio": 0}
                wv = 0.0
                for j, c in enumerate(EXTRA):
                    if c in weights:
                        wv += z[j] * weights[c]
                signed = wv / (len(ext_cols) + 1e-9)
                combo_score = s * 1.0 + signed * 0.5
            else:
                combo_score = s
            comb_pred = 1 if combo_score > 0 else 0
            comb_c += int(comb_pred == actual); comb_t += 1

        base_t = 0; up_n = 0
        for i in range(first, last + 1):
            if i >= moex_start_i:
                continue
            pos = np.searchsorted(valid, i)
            if pos >= len(valid) or valid[pos] != i:
                continue
            a = 1 if lp[i + N] > lp[i] else 0
            up_n += a; base_t += 1
        base = max(up_n, base_t - up_n) / base_t if base_t else 0.5
        print(f"\nN={N}  无MOEX窗口 {cur_t}")
        print(f"  当前fallback(均值回复)   {cur_c/cur_t*100:5.1f}%")
        print(f"  每日组合(扩展)          {comb_c/comb_t*100:5.1f}%")
        print(f"  多数类基线               {base*100:5.1f}%")


if __name__ == "__main__":
    main()