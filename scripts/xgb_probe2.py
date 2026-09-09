"""XGB 投票层精调验证(探针2)。

前版 xgb_probe.py 用 gap=25 粗粒度滚动重训, 低估了 XGB。本版:
1. 超参网格在"历史内段"(<=cutoff)内做时间顺序 walk-forward 选参(不碰评估段未来)。
2. 在选中最优参数上, 用全样本做更细 gap 滚动(gap=10)的诚实无前视评估。
3. 报告 vs 产品真实规则(MoexDirectionPredictor, run_direction_backtest 口径)的对比,
   并只在与规则同口径的 MOEX 覆盖窗口上对比(规则是主场时才能算数)。

无前视纪律同前版: 训练只用 s+N<=i; early-stop 用时间上晚于其训练段但仍 <=i-N 的样本。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from app import config
from app.data import store
from app.data.features import build_features
from app.data.moex_rates import load_moex, load_moex_hl
from app.models.mean_reversion import synthetic_meanrev_score
from app.models.moex_dir import MoexDirectionPredictor

MREV_FEATS = ["ma60_dev", "ma20_dev", "mom20", "pos60", "cusd_mom20"]


def build_ctx():
    store.init_db()
    df = store.load_rates()
    ctx = dict(df=df)
    lp = np.log(df["cny_rub"].to_numpy(float))
    dates = df.index
    Fdf = build_features(df, oil_df=store.load_oil(),
                         sentiment_df=store.load_daily_sentiment(),
                         rate_df=store.load_key_rate())
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float)
    feat_names = list(Fdf.columns)
    ctx.update(lp=lp, dates=dates, Xf=Xf, valid=valid, feat_names=feat_names)
    pred = MoexDirectionPredictor()
    pred.attach_moex([d.strftime("%Y-%m-%d") for d in dates], lp,
                     load_moex(), hl_map=load_moex_hl())
    ctx["pred"] = pred
    return ctx


def feat_matrix(ctx, N, first, last):
    """一次性预计算所有评估点特征 + 标签 + 规则参照方向/置信/信号。返回数组或 None。"""
    lp, Xf, valid, feat_names = ctx["lp"], ctx["Xf"], ctx["valid"], ctx["feat_names"]
    pred = ctx["pred"]
    rows = []
    names = None
    for i in range(first, last + 1):
        pos = np.searchsorted(valid, i)
        if pos >= len(valid) or valid[pos] != i:
            continue
        F = []
        # MOEX z
        dev_i = None
        if pred._moex_dev is not None:
            if i in pred._moex_dev:
                dev_i = pred._moex_dev[i]
            else:
                for j in range(i - 1, max(i - 4, -1), -1):
                    if j in pred._moex_dev:
                        dev_i = pred._moex_dev[j]
                        break
        z = az = 0.0
        if dev_i is not None:
            past = [rd for (j, rd) in pred._dev_hist if j <= i]
            if len(past) >= 60:
                arr = np.array(past[-150:])
                mu = arr.mean(); sd = arr.std() + 1e-9
                z = (dev_i - mu) / sd
                az = abs(z)
        F += [z, az]
        # meanrev synthetic score
        cols = [feat_names.index(c) for c in MREV_FEATS if c in feat_names]
        if len(cols) >= 3:
            sig = synthetic_meanrev_score(Xf, valid, i, cols)
            F += [sig["score"], min(sig["strength"], 5.0)]
        else:
            F += [0.0, 0.0]
        for c in ["brent_ret20", "brent_ret5", "sentiment_7d", "sentiment_trend",
                  "rate_change", "usd_ret20", "pos60", "mom20", "rsi14",
                  "brent_vol_ratio", "cny_usd_beta60"]:
            if c in feat_names:
                v = Xf[i, feat_names.index(c)]
                v = 0.0 if np.isnan(v) else float(v)
            else:
                v = 0.0
            F.append(v)
        # 规则参照(MOEX 或 fallback 信号)
        r = pred.predict_direction({"lp": lp, "i": i, "Xf": Xf,
                                    "valid": valid, "feat_names": feat_names}, N)
        sig_kind = r.get("signal", "none")
        if sig_kind == "moex_dev":
            F += [float(r.get("z", 0.0)), float(r.get("confirms", 0))]
            rule_pred = r["prediction"]
            rule_flag = 0
        else:
            F += [0.0, 0.0]
            rule_pred = int(r["prediction"]) if "prediction" in r else 0
            rule_flag = 1  # 非 MOEX 信号
        yield i, np.array(F, float), (1 if lp[i + N] > lp[i] else 0), \
            rule_pred, rule_flag, r.get("confidence", 0.5)


def rolling_eval(ctx, N, params, gap):
    """细/选定参数滚动 XGB 评估。返回 pred 概率数组随 idxs。"""
    lp = ctx["lp"]
    m = len(lp)
    first = max(config.MIN_TRAIN, int(ctx["valid"][0]) if len(ctx["valid"]) else 0)
    last = m - 1 - N
    import xgboost as xgb

    data = list(feat_matrix(ctx, N, first, last))
    if not data:
        return None
    idxs = np.array([d[0] for d in data])
    feats = np.array([d[1] for d in data])
    labels = np.array([d[2] for d in data])
    rule_preds = np.array([d[3] for d in data])
    rule_flags = np.array([d[4] for d in data])  # 0=moex_dev
    rule_conf = np.array([d[5] for d in data])

    preds = np.full(len(idxs), -1.0)
    model = None
    retrain_i = None
    for t in range(len(idxs)):
        i = idxs[t]
        if model is None or (retrain_i is not None and i - retrain_i >= gap):
            tr = idxs <= i - N
            if tr.sum() < 300:
                model = None
                retrain_i = i
                continue
            Xtr, ytr = feats[tr], labels[tr]
            # 时间顺序切: 训练 82%, early-stop 尾 18%(仍 <= i-N)
            cut = max(300, int(len(Xtr) * 0.82))
            m = xgb.XGBClassifier(
                n_estimators=200, max_depth=params["max_depth"],
                learning_rate=params["lr"], subsample=0.85,
                colsample_bytree=params["cbt"], min_child_weight=params["mcw"],
                random_state=0, early_stopping_rounds=25, eval_metric="logloss",
            )
            m.fit(Xtr[:cut], ytr[:cut], eval_set=[(Xtr[cut:], ytr[cut:])],
                  verbose=False)
            model = m
            retrain_i = i
        if model is not None:
            preds[t] = model.predict_proba(feats[t:t + 1])[0, 1]
    return dict(idxs=idxs, feats=feats, labels=labels, preds=preds,
                rule_preds=rule_preds, rule_vs=rule_preds == labels,
                rule_moex=rule_flags == 0, rule_conf=rule_conf)


def select_params(ctx, N, cutoff_frac=0.7, gap=30):
    """在历史内段选参(时间 walk)。cutoff 前的数据当作"已结束", 参数选择只看它。"""
    lp = ctx["lp"]
    m = len(lp)
    first = max(config.MIN_TRAIN, int(ctx["valid"][0]) if len(ctx["valid"]) else 0)
    last = int(first + (m - 1 - N - first) * cutoff_frac)
    import xgboost as xgb
    data = list(feat_matrix(ctx, N, first, last))
    idxs = np.array([d[0] for d in data])
    feats = np.array([d[1] for d in data])
    labels = np.array([d[2] for d in data])
    grid = [{"max_depth": 3, "lr": 0.12, "cbt": 0.9, "mcw": 1},
            {"max_depth": 3, "lr": 0.12, "cbt": 0.7, "mcw": 7},
            {"max_depth": 4, "lr": 0.1, "cbt": 0.8, "mcw": 3},
            {"max_depth": 5, "lr": 0.05, "cbt": 0.8, "mcw": 1},
            {"max_depth": 5, "lr": 0.05, "cbt": 0.7, "mcw": 5},
            {"max_depth": 6, "lr": 0.05, "cbt": 0.8, "mcw": 5}]
    best = None
    best_acc = -1
    for params in grid:
        preds = np.full(len(idxs), -1.0)
        model = None; ri = None
        for t in range(len(idxs)):
            i = idxs[t]
            if model is None or (ri is not None and i - ri >= gap):
                tr = idxs <= i - N
                if tr.sum() < 300:
                    model = None; ri = i; continue
                Xtr, ytr = feats[tr], labels[tr]
                cut = max(300, int(len(Xtr) * 0.82))
                mm = xgb.XGBClassifier(
                    n_estimators=200, max_depth=params["max_depth"],
                    learning_rate=params["lr"], subsample=0.85,
                    colsample_bytree=params["cbt"], min_child_weight=params["mcw"],
                    random_state=0, early_stopping_rounds=25, eval_metric="logloss")
                mm.fit(Xtr[:cut], ytr[:cut], eval_set=[(Xtr[cut:], ytr[cut:])],
                       verbose=False)
                model = mm; ri = i
            if model is not None:
                preds[t] = model.predict_proba(feats[t:t + 1])[0, 1]
        m2 = preds >= 0
        acc = (((preds[m2] > 0.5) == labels[m2]).mean()) if m2.sum() else 0
        if acc > best_acc:
            best_acc = acc; best = params
        # noqa: flush
    print(f"  [选参 N={N}] 扫 {len(grid)} 组, 段内最好 acc {best_acc*100:.1f}% "
          f"params={best}")
    return best


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--gap", type=int, default=10)
    p.add_argument("--grid-only", action="store_true")
    a = p.parse_args()
    ctx = build_ctx()

    for N in [7, 30]:
        print(f"\n=== N={N} ===")
        best = select_params(ctx, N)
        if a.grid_only:
            continue
        res = rolling_eval(ctx, N, best, a.gap)
        if res is None:
            print("  数据不足"); continue
        m2 = res["preds"] >= 0
        # 全口径
        all_xgb = (((res["preds"][m2] > 0.5) == res["labels"][m2]).mean()
                   if m2.sum() else 0)
        # 规则全口径(产品回测真实口径; rule_moex 0 处 z-sign 已含, 非moex含fallback)
        rule_all = res["rule_vs"].mean()
        # 仅在 MOEX 覆盖窗口(规则主场)对比
        mo = m2 & res["rule_moex"]
        xgb_mo = (((res["preds"][mo] > 0.5) == res["labels"][mo]).mean()) if mo.sum() else 0
        rule_mo = (res["rule_vs"][mo]).mean() if mo.sum() else 0
        # XGB 高置信(>0.62 or prob>0.62)
        hi = m2 & (np.abs(res["preds"] - 0.5) > 0.15)
        xgb_hi = (((res["preds"][hi] > 0.5) == res["labels"][hi]).mean()) if hi.sum() else 0
        hi_mo = hi & res["rule_moex"]
        rule_hi = (res["rule_vs"][hi_mo]).mean() if hi_mo.sum() else 0
        print(f"  gap={a.gap} 参数 {best}")
        print(f"  XGB 全口径 准确率 {all_xgb*100:.2f}%  (窗口 {int(m2.sum())})")
        print(f"  规则 全口径 准确率 {rule_all*100:.2f}%")
        print(f"  [MOEX覆盖窗]   XGB {xgb_mo*100:.2f}% vs 规则 {rule_mo*100:.2f}%  (w={int(mo.sum())})")
        print(f"  XGB 高置信      {xgb_hi*100:.2f}% (w={int(hi.sum())}); "
              f"同窗规则 {rule_hi*100:.2f}%")


if __name__ == "__main__":
    main()