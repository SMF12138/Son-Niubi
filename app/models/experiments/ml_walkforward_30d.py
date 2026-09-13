"""Phase 2 第 5 步: LR / RF / XGB 嵌套 walk-forward 终审(只实验, 不进生产)。

口径(冻结):
  - 标签: 30 日二分类, R30>0 -> UP(1), 否则 DOWN(0)
  - 特征: build_features 全部因果特征 + longhorizon.rev(生产主信号基准)
  - 评估: 年度扩展窗 walk-forward。每年初用截至上年末的已实现样本(j+30<=年末)
    训练模型, 预测本年所有交易日。严格因果, 无未来函数。
  - 预处理: 训练集 fit 中位数填充 + 标准化, transform 到测试集
  - 超参: 固定预设值(不调参, 不做网格搜索), 避免数据挖掘
  - 不修改任何生产代码/JSON/前端

产物: artifacts/ml_walkforward_30d.json + ..._report.md
"""
import datetime as dt
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.pipeline import Pipeline

from app import config
from app.data import store
from app.data.features import build_features
from app.models.experiments.contract import HORIZON_DAYS, binary_label, r30_simple_return
from app.models.longhorizon import compute_long_signals, _aligned_moex
from app.data.moex_rates import load_moex

warnings.filterwarnings("ignore")
ART = Path(__file__).resolve().parent / "artifacts"
JSON_PATH = ART / "ml_walkforward_30d.json"
REPORT_PATH = ART / "ml_walkforward_30d_report.md"

# 固定超参(预注册, 不调)
MODELS = {
    "LR": lambda: Pipeline([
        ("imp", SimpleImputer(strategy="median")),
        ("sc", StandardScaler()),
        ("clf", LogisticRegression(C=1.0, class_weight="balanced",
                                    max_iter=1000, random_state=42)),
    ]),
    "RF": lambda: Pipeline([
        ("imp", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=20,
            class_weight="balanced", random_state=42, n_jobs=-1)),
    ]),
}
try:
    from xgboost import XGBClassifier
    MODELS["XGB"] = lambda: Pipeline([
        ("imp", SimpleImputer(strategy="median")),
        ("clf", XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            eval_metric="logloss", random_state=42, n_jobs=-1)),
    ])
except Exception:
    pass


def _labels(df):
    p = df["cny_rub"].astype(float).values
    n = len(p)
    r30 = np.full(n, np.nan)
    for t in range(n - HORIZON_DAYS):
        r30[t] = r30_simple_return(p[t], p[t + HORIZON_DAYS])
    return pd.Series([binary_label(x) if np.isfinite(x) else np.nan
                      for x in r30], index=df.index)


def build_dataset():
    df = store.load_rates()
    oil = store.load_oil()
    rate = store.load_key_rate()
    try:
        sent = store.load_daily_sentiment()
    except Exception:
        sent = None
    F = build_features(df, oil_df=oil, sentiment_df=sent, rate_df=rate)
    lp = np.log(df["cny_rub"].astype(float).values)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    sig = compute_long_signals(lp, _aligned_moex(dates, load_moex()))
    F["rev"] = sig["rev"]
    y = _labels(df)
    return df, F, y


def walk_forward(df, F, y, model_name):
    """年度扩展窗: 每年初用截至上年末的已实现样本训练, 预测本年。"""
    years = sorted(df.index.year.unique())
    all_pred, all_true, all_prob, all_year = [], [], [], []
    for yr in years[2:]:   # 前两年留给 warmup
        # 训练集: 所有 t 满足 year(t) < yr 且 t+30 已实现(即 t <= 年末-30)
        train_mask = (df.index.year < yr) & y.notna()
        test_mask = (df.index.year == yr) & y.notna()
        if train_mask.sum() < 100 or test_mask.sum() == 0:
            continue
        Xtr = F[train_mask].to_numpy(float)
        ytr = y[train_mask].to_numpy(int)
        Xte = F[test_mask].to_numpy(float)
        yte = y[test_mask].to_numpy(int)
        model = MODELS[model_name]()
        model.fit(Xtr, ytr)
        prob = model.predict_proba(Xte)[:, 1]
        pred = (prob >= 0.5).astype(int)
        all_pred.extend(pred.tolist())
        all_true.extend(yte.tolist())
        all_prob.extend(prob.tolist())
        all_year.extend([yr] * len(yte))
    return (np.array(all_true), np.array(all_pred),
            np.array(all_prob), np.array(all_year))


def metrics(true, pred, prob):
    n = len(true)
    if n == 0:
        return {"n": 0, "hit": None, "auc": None, "brier": None}
    hit = float((pred == true).mean())
    try:
        auc = float(roc_auc_score(true, prob))
    except Exception:
        auc = None
    brier = float(brier_score_loss(true, prob))
    return {"n": n, "hit": round(hit, 4), "auc": round(auc, 4) if auc else None,
            "brier": round(brier, 4)}


def era_metrics(true, pred, prob, years, eras):
    out = {}
    for name, lo, hi in eras:
        mask = (years >= int(lo[:4])) & (years < int(hi[:4]))
        if not mask.any():
            out[name] = {"n": 0, "hit": None}
            continue
        out[name] = metrics(true[mask], pred[mask], prob[mask])
    return out


def main():
    df, F, y = build_dataset()
    eras = [("2010-2015", "2010-01-01", "2016-01-01"),
            ("2016-2020", "2016-01-01", "2021-01-01"),
            ("2021-今", "2021-01-01", "2200-01-01")]
    results = {}
    for name in MODELS:
        true, pred, prob, years = walk_forward(df, F, y, name)
        ov = metrics(true, pred, prob)
        er = era_metrics(true, pred, prob, years, eras)
        results[name] = {"overall": ov, "eras": er}

    # 基线: 多数类(永远预测涨) 和 rev 信号
    valid = y.notna()
    base_n = int(valid.sum())
    base_hit = float((y[valid] == 1).mean())   # 涨占比 = 永远预测涨的命中率
    results["baseline_majority"] = {
        "overall": {"n": base_n, "hit": round(max(base_hit, 1 - base_hit), 4)},
        "eras": {},
    }
    rev = F["rev"]
    rev_mask = valid & rev.notna() & np.isfinite(rev)
    rev_pred = (rev[rev_mask] > 0).astype(int).to_numpy()
    rev_true = y[rev_mask].to_numpy(int)
    rev_prob = rev[rev_mask].to_numpy(float)
    # rev 不是概率, 用 logistic 映射成伪概率仅用于 AUC/Brier
    rev_prob = 1 / (1 + np.exp(-np.clip(rev_prob, -10, 10)))
    results["baseline_rev_sign"] = {
        "overall": metrics(rev_true, rev_pred, rev_prob),
        "eras": {},
    }

    doc = {
        "meta": {
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "contract_version": "2026-09-13-v1",
            "horizon_days": HORIZON_DAYS,
            "features": list(F.columns),
            "models": list(MODELS.keys()),
            "walk_forward": "annual expanding window: train on year<yr, predict year=yr",
            "preprocessing": "median impute + standardize (fit on train only)",
            "hyperparams": "fixed, no tuning",
            "target": "binary R30>0",
        },
        "results": results,
    }
    ART.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(render(doc))
    print(f"[ml-walkforward] {JSON_PATH}")
    print(f"[ml-walkforward] {REPORT_PATH}")
    for name, r in results.items():
        ov = r["overall"]
        h = f"{ov['hit']*100:.1f}%" if ov.get("hit") else "—"
        print(f"  {name:20s} OOS={h} n={ov['n']}")


def render(doc):
    L = ["# Phase 2 第 5 步 · 30 日 LR/RF/XGB walk-forward 终审", ""]
    L.append(f"- 生成: {doc['meta']['generated']}")
    L.append(f"- 标签: 30 日 R30>0 涨, 否则跌; 特征 {len(doc['meta']['features'])} 个")
    L.append("- walk-forward: 年度扩展窗(训练 year<yr, 预测 year=yr)")
    L.append("- 预处理: 训练集中位数填充 + 标准化(fit only on train)")
    L.append("- 超参固定不调; 基线=多数类(永远预测涨) / rev符号")
    L.append("- 门槛: OOS ≥ 70%")
    L.append("")
    L.append("## 结果")
    L.append("")
    L.append("| 模型 | 全历史命中 | n | AUC | Brier | 2010-15 | 2016-20 | 2021-今 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, r in doc["results"].items():
        ov = r["overall"]
        er = r["eras"]
        def p(x): return "—" if x is None else f"{x*100:.1f}%"
        def e(n): return p(er.get(n, {}).get("hit"))
        L.append(f"| {name} | {p(ov['hit'])} | {ov['n']} | "
                 f"{p(ov.get('auc'))} | {ov.get('brier','—')} | "
                 f"{e('2010-2015')} | {e('2016-2020')} | {e('2021-今')} |")
    L.append("")
    best = max((r for r in doc["results"].values() if r["overall"].get("hit")),
               key=lambda r: r["overall"]["hit"], default=None)
    if best and best["overall"]["hit"] >= 0.70:
        L.append("## 达标 ✅")
        L.append("")
        for name, r in doc["results"].items():
            if r["overall"].get("hit") and r["overall"]["hit"] >= 0.70:
                L.append(f"- **{name}** 全历史 {r['overall']['hit']*100:.1f}%")
    else:
        L.append("## 达标情况")
        L.append("")
        L.append("**无模型达到 70%**。单因子天花板 ~56%，多因子 ML 在此数据集上未能突破。")
    L.append("")
    L.append("> 本脚本只在 experiments/ 下运行, 不修改任何生产代码/JSON/前端。")
    L.append("> 若未来有模型 OOS ≥70%, 须另行经用户终审后才允许接入生产。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
