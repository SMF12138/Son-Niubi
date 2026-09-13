"""Phase 2 · 第 1 步：30 日标签诊断（只观察、只报告）。

硬约束（2026-09-13 用户指令）：
- 本脚本不训练任何模型、不选特征、不定权重；
- 不允许根据诊断结果自动修改 FLAT 阈值 / 样本权重 / 类别权重 / 模型参数；
- 标签口径只从 contract.py 导入（±1% 已预注册冻结）；
- 不读取也不写任何生产 JSON（forecast_* / direction_result /
  longhorizon_result / calibration），产物只写本包 artifacts/。

产出：
- artifacts/label_diagnostic_30d.json         机器可读
- artifacts/label_diagnostic_30d_report.md    人类可读

运行（项目根目录）：
    .\\.venv\\Scripts\\python.exe -m app.models.experiments.label_diagnostic
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app.data import store
from app.data.moex_rates import load_moex
from app.models.longhorizon import (
    WEAK_REGIME_LOOK, WEAK_VOL_WIN,
    _rolling_std, compute_high_vol_regime,
)
from app.models.experiments import contract as C

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
JSON_PATH = ARTIFACT_DIR / "label_diagnostic_30d.json"
REPORT_PATH = ARTIFACT_DIR / "label_diagnostic_30d_report.md"

# === 诊断专用分层口径（仅用于"标签在什么环境下长什么样"的观察， ===
# === 不是模型契约、不是特征、不是阈值；冻结于此，诊断结果不得回改本块。 ===
TREND_LOOKBACK = 60          # 用过去 60 个牌价日的对数收益定义趋势状态
TREND_HALF_WIDTH = 0.03      # >+3% 牛市 / <-3% 熊市 / 之间震荡
TREND_HALF_WIN_PCT = int(TREND_HALF_WIDTH * 100)
MODERN_YEAR = 2021           # 与既有长周期"近年代衰减"分析一致的年代切分

# 数据质量观察口径（冻结，非标签契约）：
# 相邻牌价日 |ΔlogP| > 20% 判为 CBR nominal 翻转假跳变。该阈值不是"挑"出来的——
# 全历史真实最大日变动为 +12.56%（2022-03-03 卢布危机周），而假跳变最小约 ±84.9%，
# 阈值落在 (13%, 84%) 的巨大空档内任意取值结论不变。
NOMINAL_JUMP_THRESHOLD = 0.20

_QUANTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)


def _r6(x) -> float:
    return round(float(x), 6)


def _pct(x) -> float:
    return round(float(x), 4)


def group_stats(r30: np.ndarray, y3: np.ndarray) -> dict:
    """对一组样本统计标签分布与纯度。二分类在全部样本上计（FLAT 按符号归边）。"""
    n = int(len(r30))
    if n == 0:
        return {"n": 0}
    up = int(np.sum(y3 == C.LABEL_UP))
    flat = int(np.sum(y3 == C.LABEL_FLAT))
    down = int(np.sum(y3 == C.LABEL_DOWN))
    bin_up = int(np.sum(r30 > 0))
    bin_down = n - bin_up
    nonflat_n = up + down
    return {
        "n": n,
        "up": up, "flat": flat, "down": down,
        "up_pct": _pct(up / n), "flat_pct": _pct(flat / n),
        "down_pct": _pct(down / n),
        "ternary_majority_purity": _pct(max(up, flat, down) / n),
        "binary_up": bin_up, "binary_down": bin_down,
        "binary_up_pct": _pct(bin_up / n),
        "binary_majority_purity": _pct(max(bin_up, bin_down) / n),
        # 摘掉 |R30|<=1% 的 FLAT 样本后, UP/DOWN 两子集的方向纯度
        "nonflat_n": nonflat_n,
        "nonflat_share": _pct(nonflat_n / n),
        "nonflat_directional_purity": (
            _pct(max(up, down) / nonflat_n) if nonflat_n else None),
        "mean_r30": _r6(np.mean(r30)),
        "median_r30": _r6(np.median(r30)),
    }


def distribution_stats(r30: np.ndarray) -> dict:
    """R30 整体分布统计（只描述，不评价阈值好坏）。"""
    qs = {f"q{int(q * 100):02d}": _r6(np.quantile(r30, q)) for q in _QUANTILES}
    return {
        "n": int(len(r30)),
        "mean": _r6(np.mean(r30)),
        "std": _r6(np.std(r30, ddof=1)),
        "min": _r6(np.min(r30)),
        "max": _r6(np.max(r30)),
        **qs,
        "share_positive": _pct(np.mean(r30 > 0)),
        "share_abs_le_1pct": _pct(np.mean(np.abs(r30) <= C.FLAT_HALF_WIDTH)),
        "share_abs_gt_1pct": _pct(np.mean(np.abs(r30) > C.FLAT_HALF_WIDTH)),
        "mean_abs_r30": _r6(np.mean(np.abs(r30))),
    }


def _layer_table(labels: pd.DataFrame, col: str, order=None) -> dict:
    out = {}
    keys = order if order is not None else sorted(labels[col].unique())
    for k in keys:
        sub = labels[labels[col] == k]
        out[str(k)] = group_stats(sub["r30"].to_numpy(float),
                                  sub["y3"].to_numpy(int))
    return out


def build_diagnostic(df: pd.DataFrame) -> dict:
    m = len(df)
    dates = df.index
    price = df[C.LABEL_PRICE_COLUMN].to_numpy(dtype=float)
    lp = np.log(price)
    N = C.HORIZON_DAYS

    # ---------- 1) 无法生成标签的样本（显式计数，绝不填充/伪造） ----------
    missing = {"total_rows": m, "no_future_trailing": 0,
               "nan_price_t": 0, "nan_price_t30": 0, "total_unlabelable": 0}
    rows = []
    for t in range(m):
        if t + N >= m:
            missing["no_future_trailing"] += 1
            continue
        if not np.isfinite(price[t]):
            missing["nan_price_t"] += 1
            continue
        if not np.isfinite(price[t + N]):
            missing["nan_price_t30"] += 1
            continue
        r = C.r30_simple_return(price[t], price[t + N])
        rows.append((t, dates[t], r, C.ternary_label(r)))
    missing["total_unlabelable"] = (
        missing["no_future_trailing"] + missing["nan_price_t"]
        + missing["nan_price_t30"])
    missing["labelable"] = len(rows)

    labels = pd.DataFrame(rows, columns=["t", "date", "r30", "y3"])
    r30 = labels["r30"].to_numpy(float)

    # ---------- 1b) 数据质量：CBR per-record Nominal 翻转（抓取层未做 Value/Nominal 归一化） ----------
    # 只观察、只标记，绝不修改价格或标签；污染窗口与干净窗口双口径并列报告。
    dlog = np.abs(np.diff(lp))
    dlog_idx = np.where(dlog > NOMINAL_JUMP_THRESHOLD)[0]   # i: 跳变发生在 i -> i+1 之间
    flagged_days = [{
        "date_before": dates[i].date().isoformat(),
        "date_after": dates[i + 1].date().isoformat(),
        "simple_move": _r6(price[i + 1] / price[i] - 1.0),
    } for i in dlog_idx]
    # 真实（非翻转）最大相邻日变动，作为阈值空档的证据
    real_mask = np.ones(len(dlog), dtype=bool)
    real_mask[dlog_idx] = False
    largest_real_move = _r6(np.exp(np.max(dlog[real_mask])) - 1.0) \
        if real_mask.any() else None

    # 标签窗口 [t, t+30] 内只要包含任一跳变边界 i->i+1（t<=i<=t+29），即判污染。
    contaminated_t = set()
    for i in dlog_idx:
        for t in range(max(0, i - N + 1), min(i, m - N - 1) + 1):
            contaminated_t.add(t)
    labels["clean"] = [t not in contaminated_t for t in labels["t"]]

    # ---------- 2) 波动 regime（直接复用生产的因果掩码 compute_high_vol_regime） ----------
    hv = compute_high_vol_regime(lp)
    rets = np.diff(lp, prepend=lp[0])
    rets[0] = np.nan
    vol20 = _rolling_std(rets, WEAK_VOL_WIN)
    vol_state = []
    for t in labels["t"]:
        if t < WEAK_REGIME_LOOK or not np.isfinite(vol20[t]):
            vol_state.append("unknown_warmup")
        else:
            vol_state.append("high_vol" if bool(hv[t]) else "normal_vol")
    labels["vol_regime"] = vol_state

    # ---------- 3) 牛/熊/震荡（项目内无既有定义；诊断专用因果口径，见模块常量） ----------
    trend_state = []
    for t in labels["t"]:
        if t < TREND_LOOKBACK:
            trend_state.append("unknown_warmup")
            continue
        mom = lp[t] - lp[t - TREND_LOOKBACK]
        if mom > TREND_HALF_WIDTH:
            trend_state.append("bull")
        elif mom < -TREND_HALF_WIDTH:
            trend_state.append("bear")
        else:
            trend_state.append("sideways")
    labels["trend_regime"] = trend_state

    # ---------- 4) 年代层 ----------
    labels["year"] = [pd.Timestamp(d).year for d in labels["date"]]
    labels["era"] = [f"modern_{MODERN_YEAR}plus"
                     if pd.Timestamp(d).year >= MODERN_YEAR else f"pre{MODERN_YEAR}"
                     for d in labels["date"]]

    # ---------- 5) MOEX 在岸价可得性（t 当日确有成交；精确对齐，不做 ≤3 日回填） ----------
    moex_map = load_moex()
    labels["moex_at_t"] = [
        "yes" if d.strftime("%Y-%m-%d") in moex_map else "no"
        for d in labels["date"]]

    layer_specs = [
        ("overall", "all", None),
        ("by_year", "year", None),
        ("by_era", "era", [f"pre{MODERN_YEAR}", f"modern_{MODERN_YEAR}plus"]),
        ("vol_regime", "vol_regime",
         ["high_vol", "normal_vol", "unknown_warmup"]),
        ("trend_regime", "trend_regime",
         ["bull", "sideways", "bear", "unknown_warmup"]),
        ("moex_at_t", "moex_at_t", ["yes", "no"]),
    ]

    def _all_layers(frame: pd.DataFrame) -> dict:
        out = {}
        for name, col, order in layer_specs:
            if name == "overall":
                out[name] = {"all": group_stats(
                    frame["r30"].to_numpy(float), frame["y3"].to_numpy(int))}
            else:
                out[name] = _layer_table(frame, col, order)
        return out

    clean = labels[labels["clean"]]
    layers_raw = _all_layers(labels)
    layers_clean = _all_layers(clean)

    n_contam = int((~labels["clean"]).sum())
    data_quality = {
        "scan": "CBR per-record <Nominal> flip regression guard: "
                "official rate = Value/Nominal (fixed in fetcher 2026-09-13). "
                "This scan stays as a permanent guard; it never mutates any threshold.",
        "observation_only": True,
        "jump_threshold_log_move": NOMINAL_JUMP_THRESHOLD,
        "largest_real_daily_move": largest_real_move,
        "flagged_jump_days": flagged_days,
        "n_flagged_jump_days": len(flagged_days),
        "contaminated_label_windows": n_contam,
        "clean_label_windows": int(labels["clean"].sum()),
        "contamination_rule": "label t contaminated if window [t, t+30] contains "
                              "any flagged jump boundary",
    }

    return {
        "meta": {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "step": "phase2_step1_label_diagnostic",
            "observation_only": True,
            "contract_version": C.CONTRACT_VERSION,
            "label_contract": {
                "horizon_trading_days": C.HORIZON_DAYS,
                "return_definition": "R30=(P(t+30)-P(t))/P(t), CBR official CNY/RUB",
                "flat_half_width_frozen": C.FLAT_HALF_WIDTH,
                "ternary": "UP if R30>+1%, DOWN if R30<-1%, else FLAT (boundary->FLAT)",
                "binary": "UP if R30>0 else DOWN (no abstain)",
            },
            "diagnostic_regime_definitions": {
                "vol_regime": "reuse production compute_high_vol_regime: "
                              "20d return std > median of prior 252d (causal)",
                "trend_regime": f"diagnostic-only frozen: past {TREND_LOOKBACK}-day "
                                 f"log return >+{TREND_HALF_WIN_PCT}% bull / "
                                 f"< -{TREND_HALF_WIN_PCT}% bear / else sideways",
                "era": f"year>={MODERN_YEAR} vs earlier",
                "moex_at_t": "MOEX CNYRUB_TOM close exists on exact date t",
            },
            "data_as_of": dates[-1].date().isoformat(),
            "data_first_date": dates[0].date().isoformat(),
        },
        "missing": missing,
        "data_quality": data_quality,
        "r30_distribution_raw": distribution_stats(r30),
        "r30_distribution_clean": distribution_stats(
            clean["r30"].to_numpy(float)),
        "layers_raw": layers_raw,
        "layers_clean": layers_clean,
    }


# ----------------- 人类可读报告 -----------------

def _group_row(name: str, g: dict) -> str:
    if g.get("n", 0) == 0:
        return f"| {name} | 0 | — | — | — | — | — |"
    return (f"| {name} | {g['n']} | "
            f"{g['up']} ({g['up_pct']*100:.1f}%) | "
            f"{g['flat']} ({g['flat_pct']*100:.1f}%) | "
            f"{g['down']} ({g['down_pct']*100:.1f}%) | "
            f"{g['ternary_majority_purity']*100:.1f}% | "
            f"{g['binary_majority_purity']*100:.1f}% |"
            f"{(g['nonflat_directional_purity'] or 0)*100:.1f}% |")


def _dist_table(dist: dict) -> list:
    _q_keys = ("q01", "q05", "q10", "q25", "q50", "q75", "q90", "q95", "q99")
    _qrow = " | ".join(f"{dist[k] * 100:.2f}%" for k in _q_keys)
    return [
        "| 统计量 | 值 |",
        "|---|---|",
        f"| n | {dist['n']} |",
        f"| 均值 | {dist['mean']*100:.2f}% |",
        f"| 标准差 | {dist['std']*100:.2f}% |",
        f"| 最小 / 最大 | {dist['min']*100:.2f}% / {dist['max']*100:.2f}% |",
        f"| 平均绝对变动 | {dist['mean_abs_r30']*100:.2f}% |",
        f"| R30>0 占比 | {dist['share_positive']*100:.1f}% |",
        f"| |R30| ≤ 1%（FLAT 带）占比 | {dist['share_abs_le_1pct']*100:.1f}% |",
        f"| |R30| > 1%（有方向带）占比 | {dist['share_abs_gt_1pct']*100:.1f}% |",
        "",
        "| 分位 | 1% | 5% | 10% | 25% | 50% | 75% | 90% | 95% | 99% |",
        "|---|---|---|---|---|---|---|---|---|---|",
        "| R30 | " + _qrow + " |",
    ]


def render_report(doc: dict) -> str:
    m = doc["meta"]; miss = doc["missing"]; dq = doc["data_quality"]
    dist_raw = doc["r30_distribution_raw"]; dist_clean = doc["r30_distribution_clean"]
    L_raw = doc["layers_raw"]; L_clean = doc["layers_clean"]
    lines = [
        "# 30 日标签诊断报告（Phase 2 · 第 1 步）",
        "",
        f"- 生成时间：{m['generated']}",
        f"- 数据区间：{m['data_first_date']} → {m['data_as_of']}",
        f"- 口径契约版本：`{m['contract_version']}`（±1% FLAT 已预注册冻结，本报告不回改）",
        "- 性质：**只观察、只报告**。本报告不选阈值、不调权重、不训练模型、不改生产数据。",
        "",
        "## 0. 冻结的标签定义",
        "",
        "- R30 = (P(t+30) − P(t)) / P(t)，P = CBR 官方人民币兑卢布牌价，30 个实际牌价日。",
        "- 三分类：R30 > +1% → UP；R30 < −1% → DOWN；其余（含恰好 ±1%）→ FLAT。",
        "- 二分类（对照）：R30 > 0 → UP，否则 DOWN，无弃权。",
        "",
        "## 1. 样本与缺失",
        "",
        "| 项 | 数量 |",
        "|---|---|",
        f"| 价格总行数 | {miss['total_rows']} |",
        f"| 可生成 R30 标签 | {miss['labelable']} |",
        f"| 末尾 30 行无未来价（不可标） | {miss['no_future_trailing']} |",
        f"| t 日价格缺失 | {miss['nan_price_t']} |",
        f"| t+30 日价格缺失 | {miss['nan_price_t30']} |",
        f"| 不可标合计 | {miss['total_unlabelable']} |",
        "",
    ]
    if dq["n_flagged_jump_days"] == 0:
        lines += [
            "## 2. 数据质量守卫：CBR Nominal 归一化扫描（通过）",
            "",
            "- 生产抓取 `app/data/fetcher.py` 已按官方契约 Value/Nominal 归一化（2026-09-13 修复）。",
            f"- 守卫口径（冻结观察值，非调参）：相邻牌价日 |ΔlogP| > {NOMINAL_JUMP_THRESHOLD:.0%} 即报警；",
            f"  当前扫描结果：**0** 个伪跳变；全历史最大真实日变动 "
            f"{dq['largest_real_daily_move']*100:.2f}%（2014-12 卢布危机）。",
            f"- 受污染 30 日标签窗口：**0**；raw 与 clean 完全一致，双口径仅作守卫留档。",
            "- 历史缺陷与修复对照见 `artifacts/nominal_fix/nominal_fix_audit_report.md`。",
            "",
            "## 3. R30 分布（raw）",
            "",
        ]
    else:
        lines += [
            "## 2. ⚠️ 数据质量警报：CBR Nominal 翻转（抓取层未归一化）",
            "",
            f"- 现象：{dq['n_flagged_jump_days']} 个相邻牌价日出现 ±85%~+893% 的假跳变（正确价的 10 倍/1/10 倍关系）。",
            "- 正确牌价 = Value / Nominal；生产 fetcher 只读 `<Value>` 时全链路吃未归一化序列。",
            f"- 判定口径（冻结观察值）：相邻日 |ΔlogP| > {NOMINAL_JUMP_THRESHOLD:.0%}；",
            f"  全历史**真实**最大日变动仅 {dq['largest_real_daily_move']*100:.2f}%，假跳变最小约 84.9%，阈值空档巨大、非调参。",
            f"- 受污染 30 日标签窗口：**{dq['contaminated_label_windows']}** 个；",
            f"  干净窗口：**{dq['clean_label_windows']} 个。规则：窗口 [t,t+30] 内含任一跳变边界即判污染。",
            "- 处置：只标记不自动改任何阈值/权重/参数。下文给 raw / clean 双口径。",
            "",
            "## 3. R30 分布（raw：含 nominal 污染）",
            "",
        ]
    lines += _dist_table(dist_raw)
    if dq["n_flagged_jump_days"] == 0:
        lines += ["", "## 4. R30 分布（clean：与 raw 相同，留档）", ""]
    else:
        lines += ["", "## 4. R30 分布（clean：已剔除污染窗口，后续实验的诚实底物）", ""]
    lines += _dist_table(dist_clean)

    raw_tag = "raw" if dq["n_flagged_jump_days"] == 0 else "raw（含污染）"
    clean_tag = ("clean（与 raw 相同，留档）" if dq["n_flagged_jump_days"] == 0
                 else "clean（已剔除污染窗口）")

    def _section(title: str, layer_key: str, hint=None) -> list:
        out = [f"## {title}", ""]
        if hint:
            out += [f"_{hint}_", ""]
        out += [f"**{raw_tag}**", "",
                "| 层 | n | UP | FLAT | DOWN | 三分类纯度 | 二分类纯度 | 去FLAT纯度 |",
                "|---|---|---|---|---|---|---|---|"]
        for name, g in L_raw[layer_key].items():
            out.append(_group_row(name, g))
        out += ["", f"**{clean_tag}**", "",
                "| 层 | n | UP | FLAT | DOWN | 三分类纯度 | 二分类纯度 | 去FLAT纯度 |",
                "|---|---|---|---|---|---|---|---|"]
        for name, g in L_clean[layer_key].items():
            out.append(_group_row(name, g))
        out.append("")
        return out

    lines += ["## 5. 总体标签分布与纯度", ""]
    lines += _section("5.1 总体", "overall")[2:]
    lines += [
        "> 纯度解释：三分类多数类纯度 = 永远猜最大类的正确率基线；二分类同理；",
        "> 去 FLAT 方向纯度 = 摘掉震荡样本后 UP/DOWN 两子集中较大者占比（三分类问题真正可用的方向底物）。",
        "",
    ]
    lines += _section("6. 按年份", "by_year")
    lines += _section("7. 按年代", "by_era",
                      f"modern = {MODERN_YEAR} 年起（与既有近年代衰减分析同口径）")
    lines += _section("8. 按波动 regime（复用生产高波动因果掩码）", "vol_regime")
    lines += _section("9. 按牛/熊/震荡（诊断专用冻结口径：60 日对数收益 ±3%）",
                      "trend_regime")
    lines += _section("10. 按 t 当日 MOEX 在岸价是否可得", "moex_at_t")

    if dq["n_flagged_jump_days"] == 0:
        lines += [
            "## 11. Nominal 假跳变日清单",
            "",
            "扫描结果为 0（归一化修复后无伪跳变）。历史清单与修复对照见 "
            "`artifacts/nominal_fix/nominal_fix_audit.json`。",
        ]
    else:
        lines += [
            "## 11. 已标记的 nominal 假跳变日（完整清单见 JSON: data_quality.flagged_jump_days）",
            "",
            "| 前一牌价日 | 次一牌价日 | 库内原始变动 |",
            "|---|---|---|",
        ]
        for d in dq["flagged_jump_days"]:
            lines.append(f"| {d['date_before']} | {d['date_after']} | "
                         f"{d['simple_move']*100:+.1f}% |")
    lines += [
        "",
        "## 12. 阅读须知",
        "",
        "1. 各层的多数类纯度就是该层**零信息基线**——后续任何模型必须在同一层超过它才算有信号。",
        "2. 若某层 UP/DOWN 数量过少（n 小），纯度数字不稳定，只记录、不在此步下结论。",
        "3. 牛/熊/震荡口径仅为本诊断的观察切片，不进入任何模型，也不会被本结果回改。",
        "4. clean 口径只摘掉污染窗口，不做任何插补/改价；±1% FLAT 阈值不随本结果变化。",
        "5. 本文件与 JSON 均为实验产物，位于 experiments/artifacts/，与生产 JSON 物理隔离。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    df = store.load_rates()
    if len(df) < 100:
        raise SystemExit(f"数据不足（{len(df)} 行），无法做标签诊断")
    doc = build_diagnostic(df)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(render_report(doc))

    raw = doc["layers_raw"]["overall"]["all"]
    cln = doc["layers_clean"]["overall"]["all"]
    miss = doc["missing"]; dq = doc["data_quality"]
    print(f"[label-diagnostic] rows={miss['total_rows']} labelable={miss['labelable']} "
          f"unlabelable={miss['total_unlabelable']}")
    print(f"[label-diagnostic] NOMINAL flip days={dq['n_flagged_jump_days']} "
          f"contaminated windows={dq['contaminated_label_windows']} "
          f"clean={dq['clean_label_windows']}")
    for tag, o in (("raw  ", raw), ("clean", cln)):
        print(f"[label-diagnostic] {tag} UP={o['up']} ({o['up_pct']*100:.1f}%)  "
              f"FLAT={o['flat']} ({o['flat_pct']*100:.1f}%)  "
              f"DOWN={o['down']} ({o['down_pct']*100:.1f}%)  "
              f"binPurity={o['binary_majority_purity']*100:.1f}%  "
              f"nonflatPurity={o['nonflat_directional_purity']*100:.1f}%")
    print(f"[label-diagnostic] JSON  -> {JSON_PATH}")
    print(f"[label-diagnostic] report-> {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
