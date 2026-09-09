"""每日可用外部信号探针：验证在【无 MOEX 时期】(2010-2022) 用"每天都有值"的信号
(USD/RUB 交叉 + Brent 油价 + 通用动量/趋势) 能否作为主导方向信号, 显著优于当前
纯均值回复 fallback 以及多数类基线。

目的：回答"引入每日外部信号打不打得住无MOEX覆盖空窗"。
无前视: 特征只用 <=i 的滚动历史。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from app.data import store
from app.data.features import build_features


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

    # MOEX 起始(2022-06): 划分有/无 MOEX 时期
    moex_start_i = int(np.searchsorted(dates, "2022-06-01"))

    # 每个 horizon 评估 4 种每日信号的独立区分力
    for N in [7, 30, 60]:
        last = m - 1 - N
        first = 300
        rows = []
        for i in range(first, last + 1):
            pos = np.searchsorted(valid, i)
            if pos >= len(valid) or valid[pos] != i:
                continue
            actual = 1 if lp[i + N] > lp[i] else 0
            r = {"i": i, "actual": actual, "no_moex": i < moex_start_i}
            # 油价 20 日动量方向
            for c in ["brent_ret20", "brent_ret5"]:
                if c in feat_names:
                    v = Xf[i, feat_names.index(c)]
                    r[c] = 0 if np.isnan(v) or v == 0 else (1 if v > 0 else 0)
            # USD/RUB 交叉(隐含 CNY/USD 动量 + USD/RUB 动量)
            for c in ["cusd_mom20", "usd_ret20", "usd_ret5"]:
                if c in feat_names:
                    v = Xf[i, feat_names.index(c)]
                    r[c] = 0 if np.isnan(v) or v == 0 else (1 if v > 0 else 0)
            # CNY/RUB 自身动量
            v = Xf[i, feat_names.index("mom20")]
            r["cn_mom20"] = 0 if np.isnan(v) else (1 if v > 0 else 0)
            rows.append(r)

        def _acc(rows_, key):
            if not rows_:
                return 0.0, 0
            hits = sum(1 for r in rows_ if r[key] == r["actual"])
            return hits / len(rows_), len(rows_)

        tot = _acc(rows, "actual")  # key unused => majority baseline below
        ups = sum(1 for r in rows if r["actual"] == 1)
        base = max(ups, len(rows) - ups) / len(rows) if rows else 0

        sep = rows[:0 + int(len(rows) * 0.69)]   # 2010-2022 前段(无MOEX)
        rem = rows[int(len(rows) * 0.69):]

        # 在所有时期 & 无MOEX时期 对比
        cols = ["brent_ret20", "brent_ret5", "cusd_mom20", "usd_ret20",
                "usd_ret5", "cn_mom20"]
        print(f"\n=== N={N}  全区间窗口 {len(rows)} (多数类基线 {base*100:.1f}%) ===")
        for c in cols:
            a_all, n_all = _acc(rows, c)
            # 无MOEX主管辖期(2010-2022)
            a_nm, n_nm = _acc([r for r in sep if r.get("no_moex")], c)
            if n_nm >= 200:
                print(f"  {c:14s} 全史 {a_all*100:5.1f}%({n_all}) | 无MOEX期 {a_nm*100:5.1f}%({n_nm})" +
                      ("  ★" if a_nm - base > 0.03 else ""))


if __name__ == "__main__":
    main()