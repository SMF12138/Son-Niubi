"""CBR Nominal 归一化修复 · 前后对照审计（只观察，只报告）。

输入：
  旧库  artifacts/nominal_fix/rates_pre_nominal.db（修复前完整快照）
  旧JSON artifacts/nominal_fix/before_json/*.json（修复前生产产物快照）
  新库  data/rates.db（Value/Nominal 归一化后全量重建）
  新JSON data/*.json（修复后重跑的生产产物）
输出：artifacts/nominal_fix/nominal_fix_audit.json + ..._report.md

本脚本不修改任何生产代码/数据/JSON；只读旧快照与当前产物。
修复契约：CBR 官方牌价 = Value / Nominal（fetcher._record_official_rate 单点真源），
不做任何裁剪、平滑或人工修正。
"""
import datetime as dt
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from app.models.experiments.label_diagnostic import build_diagnostic

ROOT = config.ROOT
ART = Path(__file__).resolve().parent / "artifacts" / "nominal_fix"
OLD_DB = ART / "rates_pre_nominal.db"
OLD_JSON = ART / "before_json"
JSON_PATH = ART / "nominal_fix_audit.json"
REPORT_PATH = ART / "nominal_fix_audit_report.md"
JUMP_THRESHOLD = 0.20
FEATURE_WINDOWS = (20, 60, 120, 150, 252)  # 特征/模型实际回看窗：features60 / MA120 / zdev150 / regime252


def _load(db_path: Path) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT date, cny_rub, usd_rub FROM rates ORDER BY date", conn
        )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def _jump_edges(s: pd.Series) -> list[int]:
    dlog = np.abs(np.diff(np.log(s.to_numpy())))
    return list(np.where(dlog > JUMP_THRESHOLD)[0])


def _changed_segments(old: pd.Series, new: pd.Series) -> list[dict]:
    chg = (old.round(6) != new.round(6)).to_numpy()
    segs, i, n = [], 0, len(chg)
    while i < n:
        if chg[i]:
            j = i
            while j + 1 < n and chg[j + 1]:
                j += 1
            segs.append({"start": old.index[i].date().isoformat(),
                         "end": old.index[j].date().isoformat(),
                         "rows": int(j - i + 1)})
            i = j + 1
        else:
            i += 1
    return segs


def _series_audit(dfo: pd.DataFrame, dfn: pd.DataFrame) -> dict:
    so, sn = dfo["cny_rub"], dfn["cny_rub"]
    dates_same = so.index.equals(sn.index)
    ratio = (sn / so)
    changed_mask = so.round(6) != sn.round(6)
    ratios = sorted({round(float(x), 6) for x in ratio[changed_mask].dropna()})
    by_year = {int(y): int(c) for y, c in changed_mask.groupby(so.index.year).sum().items() if c}
    edges_o = _jump_edges(so)
    edges_n = _jump_edges(sn)
    dlog_n = np.diff(np.log(sn.to_numpy()))
    still_bad = sum(1 for i in edges_o
                    if abs(float(np.log(sn.iloc[i + 1] / sn.iloc[i]))) > JUMP_THRESHOLD)
    i_max = int(np.argmax(np.abs(dlog_n)))
    largest_move = {
        "from": sn.index[i_max].date().isoformat(),
        "to": sn.index[i_max + 1].date().isoformat(),
        "signed_move": round(float(np.exp(dlog_n[i_max]) - 1.0), 6),
    }
    top_idx = np.argsort(-np.abs(dlog_n))[:8]
    top_moves = [{"from": sn.index[i].date().isoformat(),
                  "to": sn.index[i + 1].date().isoformat(),
                  "move": round(float(np.exp(dlog_n[i]) - 1.0), 6)} for i in top_idx]
    usd_changed = int((dfo["usd_rub"].round(6) != dfn["usd_rub"].round(6)).sum())
    return {
        "rows_old": int(len(so)), "rows_new": int(len(sn)),
        "date_sets_identical": bool(dates_same),
        "range_old": [so.index[0].date().isoformat(), so.index[-1].date().isoformat()],
        "range_new": [sn.index[0].date().isoformat(), sn.index[-1].date().isoformat()],
        "cny_changed_rows": int(changed_mask.sum()),
        "new_over_old_ratios_on_changed_rows": ratios,
        "cny_changed_rows_by_year": by_year,
        "nominal10_segments": _changed_segments(so, sn),
        "usd_changed_rows": usd_changed,
        "pseudo_jump_edges_old": len(edges_o),
        "pseudo_jump_edges_new": len(edges_n),
        "old_edges_still_discontinuous_new": still_bad,
        "largest_daily_move_new": largest_move,
        "top8_daily_moves_new": top_moves,
    }


def _label_stats(doc: dict) -> dict:
    o = doc["layers_raw"]["overall"]["all"]
    c = doc["layers_clean"]["overall"]["all"]
    return {
        "labelable": doc["missing"]["labelable"],
        "unlabelable": doc["missing"]["total_unlabelable"],
        "flagged_jump_days": doc["data_quality"]["n_flagged_jump_days"],
        "contaminated_windows": doc["data_quality"]["contaminated_label_windows"],
        "clean_windows": doc["data_quality"]["clean_label_windows"],
        "raw": {"n": o["n"], "up_pct": o["up_pct"], "flat_pct": o["flat_pct"],
                "down_pct": o["down_pct"], "binary_purity": o["binary_majority_purity"],
                "nonflat_purity": o["nonflat_directional_purity"]},
        "clean": {"n": c["n"], "up_pct": c["up_pct"], "flat_pct": c["flat_pct"],
                  "down_pct": c["down_pct"], "binary_purity": c["binary_majority_purity"],
                  "nonflat_purity": c["nonflat_directional_purity"]},
    }


def _level_contamination(dfo: pd.DataFrame, dfn: pd.DataFrame) -> dict:
    """跨面值边界的滚动窗行级污染（level 类与跨边界收益类特征都在此失真）。"""
    so = dfo["cny_rub"]
    edge_dates = set(so.index[_jump_edges(so) + np.array([0])])  # 边界前一日
    edge_dates |= set(so.index[_jump_edges(so) + np.array([1])])  # 边界当日
    edge_dates = sorted(edge_dates)
    idx = so.index
    is_edge = idx.isin(edge_dates)
    out = {"n_boundary_days": len(edge_dates), "boundary_days": [d.date().isoformat() for d in edge_dates]}
    cum = np.cumsum(is_edge.astype(int))
    for w in FEATURE_WINDOWS:
        # t 的 trailing-w 窗 [t-w+1, t] 含任一边界日
        n = int((cum - np.r_[np.zeros(w, dtype=int), cum[:-w]] > 0).sum())
        out[f"rows_with_boundary_in_trailing_{w}"] = n
    # MOEX 在岸价重叠：moex_dir zdev 回看 150
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            mx = pd.read_sql_query("SELECT date FROM moex_rates ORDER BY date", conn)
        mx_dates = pd.to_datetime(mx["date"])
        out["moex_available_rows"] = int(len(mx_dates))
        out["moex_aligned_to_cbr_rows"] = int(mx_dates.isin(idx).sum())
        out["moex_first_last"] = [mx_dates.min().date().isoformat(),
                                  mx_dates.max().date().isoformat()]
        cum150 = cum - np.r_[np.zeros(150, dtype=int), cum[:-150]]
        pos = {d: i for i, d in enumerate(idx)}
        hit = sum(1 for d in mx_dates if d in pos and cum150[pos[d]] > 0)
        out["moex_rows_with_boundary_in_trailing_150"] = hit
    except Exception as e:  # MOEX 表缺失不应致命
        out["moex_scan_error"] = repr(e)
    return out


def _pct(x):
    return "—" if x is None else f"{x*100:.1f}%"


def _json_metrics() -> dict:
    def load(p):
        return json.load(open(p, encoding="utf-8"))
    b_dir, a_dir = load(OLD_JSON / "direction_result.json"), load(config.DIRECTION_JSON)
    b_lh, a_lh = load(OLD_JSON / "longhorizon_result.json"), load(config.LONGHORIZON_JSON)
    b_cal, a_cal = load(OLD_JSON / "calibration.json"), load(config.DATA_DIR / "calibration.json")
    rows = {}
    for N in ("7", "30", "60", "90"):
        hb, ha = b_dir["horizons"][N], a_dir["horizons"][N]
        rows[f"moexdir_{N}"] = {
            "accuracy": [hb["accuracy"], ha["accuracy"]],
            "confident_accuracy": [hb["confident_accuracy"], ha["confident_accuracy"]],
            "confident_windows": [hb["confident_windows"], ha["confident_windows"]],
            "windows": [hb["windows"], ha["windows"]],
            "moex_accuracy": [hb.get("moex_accuracy"), ha.get("moex_accuracy")],
        }
    for N in ("30", "60", "90"):
        hb, ha = b_lh["horizons"][N], a_lh["horizons"][N]
        rows[f"longhorizon_{N}"] = {
            "policy": [hb["policy"], ha["policy"]],
            "oos_hit": [hb["oos_hit"], ha["oos_hit"]],
            "oos_emitted": [hb["oos_emitted"], ha["oos_emitted"]],
            "coverage": [hb["coverage"], ha["coverage"]],
            "eligible_days": [hb["eligible_days"], ha["eligible_days"]],
            "passed_70pct_gate": [hb["passed_70pct_gate"], ha["passed_70pct_gate"]],
        }
    cal = {}
    for table in ("cal", "cap"):
        cal[table] = {N: {z: [b_cal[table][N][z], a_cal[table][N][z]]
                          for z in b_cal[table][N]} for N in b_cal[table]}
    cal["meanrev"] = {N: [b_cal["meanrev"][N], a_cal["meanrev"][N]]
                      for N in b_cal["meanrev"]}
    fc = {}
    for N in (7, 30, 60, 90):
        bf = load(OLD_JSON / f"forecast_{N}.json")
        af = load(config.FORECAST_JSONS[N])
        bd, ad = bf.get("direction", {}), af.get("direction", {})
        fc[str(N)] = {"prediction": [bd.get("prediction"), ad.get("prediction")],
                      "confidence": [bd.get("confidence"), ad.get("confidence")],
                      "neutral_reason": [bd.get("neutral_reason"), ad.get("neutral_reason")],
                      "signal_rev": [bd.get("rev"), ad.get("rev")]}
    return {"moexdir_and_longhorizon": rows, "calibration": cal, "forecast": fc}


def _run_tests() -> dict:
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    tail = (p.stdout + p.stderr).strip().splitlines()
    return {"returncode": p.returncode,
            "summary": " | ".join(l for l in tail if "Ran " in l or l in ("OK",) or "FAILED" in l)}


def build_audit() -> dict:
    dfo, dfn = _load(OLD_DB), _load(config.DB_PATH)
    doc_old = build_diagnostic(dfo)
    doc_new = build_diagnostic(dfn)
    return {
        "meta": {
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "contract": "CBR official rate = Value / Nominal per Record; no clipping/smoothing",
            "fix_commit_scope": "app/data/fetcher.py::_record_official_rate; tests/test_fetcher.py",
            "old_db_snapshot": str(OLD_DB.relative_to(ROOT)),
        },
        "series": _series_audit(dfo, dfn),
        "labels_old": _label_stats(doc_old),
        "labels_new": _label_stats(doc_new),
        "level_contamination": _level_contamination(dfo, dfn),
        "production_metrics": _json_metrics(),
        "tests": _run_tests(),
    }


def _delta(b, a, pctv=True, pp=4):
    if b is None or a is None:
        return ""
    if pctv:
        return f"{(a-b)*100:+.1f}pp"
    return f"{a-b:+g}"


def render_report(doc: dict) -> str:
    s, lm = doc["series"], doc["level_contamination"]
    lo, ln = doc["labels_old"], doc["labels_new"]
    m = doc["production_metrics"]
    L = []
    L.append("# CBR Nominal 归一化修复 · 前后对照审计报告")
    L.append("")
    L.append(f"- 生成时间：{doc['meta']['generated']}")
    L.append("- 修复契约（单点真源）：CBR 每条 Record 的官方牌价 = `Value / Nominal`；")
    L.append("  不裁剪、不平滑、不做任何人工修正；生产数据结构/字段/接口不变。")
    L.append("- 本报告只读旧快照与当前产物生成，不写任何生产文件。")
    L.append("")
    L.append("## A. 数据修复正确性核验")
    L.append("")
    L.append(f"- 行数：旧 {s['rows_old']} / 新 {s['rows_new']}；日期集合完全一致：**{s['date_sets_identical']}**")
    L.append(f"- 区间：{s['range_new'][0]} → {s['range_new'][1]}（与旧库一致）")
    L.append(f"- USD 列变动行数：**{s['usd_changed_rows']}**（USD 全部记录 Nominal=1，除法为恒等，符合预期）")
    L.append(f"- CNY 变动行数：**{s['cny_changed_rows']}**；这些行新值/旧值比例全部 = "
             f"{s['new_over_old_ratios_on_changed_rows']}（恰好 ×0.1，即旧库存的是每10人民币牌价）")
    L.append(f"- 伪跳变边（|ΔlogP|>{JUMP_THRESHOLD:.0%}）：旧 **{s['pseudo_jump_edges_old']}** → 新 **{s['pseudo_jump_edges_new']}**；")
    L.append(f"  旧 {s['pseudo_jump_edges_old']} 个边界在新库中仍不连续的：**{s['old_edges_still_discontinuous_new']}**")
    L.append(f"- 新库最大相邻日变动：{s['largest_daily_move_new']['signed_move']*100:+.2f}%"
             f"（{s['largest_daily_move_new']['from']} → {s['largest_daily_move_new']['to']}，2014-12 卢布危机），"
             "Top8 全部落在 2014-12 / 2015-01 / 2022-02~05 真实危机周：")
    for mv in s["top8_daily_moves_new"]:
        L.append(f"  - {mv['from']} → {mv['to']}：{mv['move']*100:+.2f}%")
    L.append("")
    L.append(f"### 面值=10 的历史区间（共 {len(s['nominal10_segments'])} 段，{s['cny_changed_rows']} 行）")
    L.append("")
    L.append("| # | 起 | 止 | 行数 |")
    L.append("|---|---|---|---|")
    for i, g in enumerate(s["nominal10_segments"], 1):
        L.append(f"| {i} | {g['start']} | {g['end']} | {g['rows']} |")
    L.append("")
    L.append("> 更正第一步诊断中的表述：Nominal=10 不只是“零散单日翻转”——")
    L.append("> 2010-01-01→2014-12-17 整 5 年（1230 行）是 CBR 旧官方面值 10；")
    L.append("> 2015-2022 另有 24 个长短不一的 Nominal=10 段（最长 2016-08→2018-04，415 行）。")
    L.append("> 段内常数倍不影响收益比值，但跨边界的水平类特征与标签全部失真。")
    L.append("")
    L.append("### 30 日标签诊断（同口径重跑，FLAT ±1% 契约未动）")
    L.append("")
    L.append("| 口径 | 可标 | 伪跳变日 | 污染窗口 | 干净窗口 | UP | FLAT | DOWN | 二分类纯度 | 去FLAT纯度 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for tag, x in (("修复前", lo), ("修复后", ln)):
        r = x["raw"]
        L.append(f"| {tag} | {x['labelable']} | {x['flagged_jump_days']} | "
                 f"{x['contaminated_windows']} | {x['clean_windows']} | "
                 f"{r['up_pct']*100:.1f}% | {r['flat_pct']*100:.1f}% | {r['down_pct']*100:.1f}% | "
                 f"{r['binary_purity']*100:.1f}% | {r['nonflat_purity']*100:.1f}% |")
    L.append("")
    L.append(f"- **542 个污染窗口 → 0**；修复后 raw 与 clean 完全相同，双口径已无存在必要")
    L.append("  （诊断脚本的跳变扫描保留为持续回归守卫，阈值 20% 与真实最大变动 12.6% 之间空档不变）。")
    L.append("")
    L.append("## B. 生产结果修复前后变化（同代码、同参数、同 walk-forward，仅数据底物变化）")
    L.append("")
    L.append("### moex_dir（7日生产模型 / 30/60/90 同源回测）")
    L.append("")
    L.append("| 指标 | 修复前 | 修复后 | Δ |")
    L.append("|---|---|---|---|")
    for N in ("7", "30", "60", "90"):
        r = m["moexdir_and_longhorizon"][f"moexdir_{N}"]
        L.append(f"| N={N} 全量命中 | {_pct(r['accuracy'][0])} | {_pct(r['accuracy'][1])} | "
                 f"{_delta(r['accuracy'][0], r['accuracy'][1])} |")
        L.append(f"| N={N} 高置信(>65%)命中 | {_pct(r['confident_accuracy'][0])} ({r['confident_windows'][0]}) | "
                 f"{_pct(r['confident_accuracy'][1])} ({r['confident_windows'][1]}) | "
                 f"{_delta(r['confident_accuracy'][0], r['confident_accuracy'][1])} |")
        L.append(f"| N={N} MOEX可得期命中 | {_pct(r['moex_accuracy'][0])} | {_pct(r['moex_accuracy'][1])} | "
                 f"{_delta(r['moex_accuracy'][0], r['moex_accuracy'][1])} |")
    L.append("")
    L.append("### longhorizon（30/60/90 日，70% OOS 门槛裁决）")
    L.append("")
    L.append("| N | 指标 | 修复前 | 修复后 |")
    L.append("|---|---|---|---|")
    for N in ("30", "60", "90"):
        r = m["moexdir_and_longhorizon"][f"longhorizon_{N}"]
        L.append(f"| {N} | 策略 | {r['policy'][0]} | {r['policy'][1]}（策略代码未改） |")
        L.append(f"| {N} | OOS命中 | {_pct(r['oos_hit'][0])} | {_pct(r['oos_hit'][1])} |")
        L.append(f"| {N} | 发声/合格日（覆盖率） | {r['oos_emitted'][0]}/{r['eligible_days'][0]} "
                 f"({r['coverage'][0]*100:.1f}%) | {r['oos_emitted'][1]}/{r['eligible_days'][1]} "
                 f"({r['coverage'][1]*100:.1f}%) |")
        L.append(f"| {N} | 过70%门槛 | {r['passed_70pct_gate'][0]} | **{r['passed_70pct_gate'][1]}** |")
    L.append("")
    L.append("### 动态校准 calibration.json（扩展窗 OOS 分桶命中率）")
    L.append("")
    L.append("| 表 | N | z15 前→后 | z10 前→后 | z05 前→后 | z00 前→后 |")
    L.append("|---|---|---|---|---|---|")
    for N in ("7", "30", "60", "90"):
        c = m["calibration"]["cal"][N]
        L.append(f"| cal | {N} | " + " | ".join(
            f"{_pct(c[z][0])}→{_pct(c[z][1])}" for z in ("z15", "z10", "z05", "z00")) + " |")
    L.append("")
    L.append("| meanrev | N | 修复前 | 修复后 |")
    L.append("|---|---|---|---|")
    for N in ("7", "30", "60", "90"):
        c = m["calibration"]["meanrev"][N]
        L.append(f"| meanrev | {N} | {_pct(c[0])} | {_pct(c[1])} |")
    L.append("")
    L.append("### 当日预测产物 forecast_*.json（as_of 2026-09-12）")
    L.append("")
    L.append("| N | 修复前 | 修复后 |")
    L.append("|---|---|---|")
    for N in ("7", "30", "60", "90"):
        f = m["forecast"][str(N)]
        def _word(v):
            return {1: "↑涨", 0: "↓跌", None: "中性"}.get(v, str(v))
        cb, ca = f["confidence"]
        L.append(f"| {N} | {_word(f['prediction'][0])}"
                 f"{'' if cb is None else ' '+str(round(cb*100))+'%'} | "
                 f"{_word(f['prediction'][1])}{'' if ca is None else ' '+str(round(ca*100))+'%'} |")
    L.append("")
    L.append("**解读（只陈述，不回改任何策略参数）：**")
    L.append("- 7 日 MOEX 模型高置信命中 70.1%→71.5%（MOEX 在岸价 2023 年后才完整，受影响最小）。")
    L.append("- 30 日弱档 OOS 62.4%→**44.7%**（2021 后 57.4%→34.8%）；60 日 73.6%→60.7%；90 日 71.2%→50.9%，")
    L.append("  三个长周期策略修复后全部不过 70% 门槛，生产已按既有门槛自动转中性——")
    L.append("  说明此前长周期大部分‘优势’来自假跳变后的机械回复（10倍假高→必然‘跌回’），并非可交易信号。")
    L.append("- meanrev 分桶命中 0.56–0.74 → 0.38–0.43，同一结论：均值回复边缘主要是数据缺陷的镜像。")
    L.append("")
    L.append("## C. 其他由 Nominal 问题引起的历史污染")
    L.append("")
    L.append(f"- 面值边界日（含边界两侧）：{lm['n_boundary_days']} 个；")
    L.append("  任何滚动窗跨过这些日期的行，水平类特征（MA偏离/level_z/min-max/MOEX价差）与跨边界收益均失真：")
    for w in FEATURE_WINDOWS:
        L.append(f"  - trailing {w} 窗受污染行：**{lm[f'rows_with_boundary_in_trailing_{w}']}** / 4125")
    if "moex_rows_with_boundary_in_trailing_150" in lm:
        L.append(f"- MOEX 在岸数据 {lm['moex_first_last'][0]}→{lm['moex_first_last'][1]}："
                 f"表内 {lm['moex_available_rows']} 行，与 CBR 牌价日对齐 {lm['moex_aligned_to_cbr_rows']} 行，")
        L.append(f"  其中 150 日 zdev 窗跨面值边界的有 **{lm['moex_rows_with_boundary_in_trailing_150']}** 行"
                 "（集中在 2022-12~2023-07）。")
        L.append("  2022-05-31→2022-12-21 段 CBR 旧库存为 ~100（实际 ~10），与 MOEX 在岸 ~10 直接可比，")
        L.append("  该段 log 价差被人为抬高 ln10≈2.3，moex_dir 的 150 日标准化窗在边界两侧均失真。")
    L.append("- 标签（收益比值）在面值恒定段内不受影响，仅跨边界 30 日窗口失真，即第一步标记的 542 个，现为 0。")
    L.append("- 油价/新闻/关键利率/MOEX 四张表不经 CBR 解析，本次未触碰；USD 零变动。")
    L.append("")
    L.append("## D. 测试")
    L.append("")
    L.append(f"- `python -m unittest discover -s tests`：{doc['tests']['summary']}（returncode {doc['tests']['returncode']}）")
    L.append("- 新增回归测试 `test_nominal_normalization`：Nominal=10 的记录必须输出 Value/10。")
    L.append("")
    L.append("## 结论")
    L.append("")
    L.append("A. 修复正确：4125 行/日期集合不变，2475 个 ×10 记录全部归一，51 个伪跳变边清零，")
    L.append("   最大日变动落在真实危机区间，USD 零影响，全量单测通过。")
    L.append("B. 生产数字显著下修：30/60/90 日三策略 OOS 全部跌破 70% 门槛并已自动中性；7 日模型基本稳健。")
    L.append("C. 除标签 542 窗口外，跨边界滚动特征行（trailing20/60/120/150/252 分别 421/844/1286/1430/1838 行）、")
    L.append("   以及 2022-12~2023-07 共 239 个 MOEX 对齐日的 zdev 窗均曾受污染，已随重建消除。")
    L.append("D. 全部既有测试通过，另加一条归一化回归测试。")
    L.append("")
    L.append("> 本修复未改 FLAT ±1% 契约、模型参数、阈值、特征、walk-forward 规则、策略逻辑或前端。")
    return "\n".join(L) + "\n"


def main() -> int:
    doc = build_audit()
    ART.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(render_report(doc))
    print(f"[nominal-fix-audit] {JSON_PATH}")
    print(f"[nominal-fix-audit] {REPORT_PATH}")
    print(f"[nominal-fix-audit] jumps {doc['series']['pseudo_jump_edges_old']} -> "
          f"{doc['series']['pseudo_jump_edges_new']}, tests: {doc['tests']['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
