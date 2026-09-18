"""Flask 静态页 + JSON API。所有分析产物由 cli 生成后由本服务读取。"""
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from app import config
from app.forecast import load_forecast, build_projection
from app.data import store
from app.monitor_signal import (
    WINDOW_DAYS, _longhorizon_preds, _moex7_preds,
)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(config.STATIC_DIR, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(config.STATIC_DIR, filename)

    @app.get("/api/rates")
    def api_rates():
        limit = min(max(request.args.get("limit", default=500, type=int), 30), 2000)
        df: pd.DataFrame = store.load_rates()
        tail = df.tail(max(limit, 30))
        return jsonify({
            "as_of": df.index[-1].isoformat() if len(df) else None,
            "dates": [d.date().isoformat() for d in tail.index],
            "cny": [float(v) for v in tail["cny_rub"]],
            "usd": [None if pd.isna(v) else float(v) for v in tail["usd_rub"]],
            "source_counts": _source_counts(),
        })

    @app.get("/api/direction")
    def api_direction():
        if not config.DIRECTION_JSON.exists():
            return jsonify({"error": "方向回测不存在,请运行 python -m app.cli backtest"}), 503
        import json
        try:
            with open(config.DIRECTION_JSON, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except (OSError, json.JSONDecodeError):
            return jsonify({"error": "方向回测文件读取失败"}), 503

    @app.get("/api/oil")
    def api_oil():
        limit = min(max(request.args.get("limit", default=500, type=int), 30), 2000)
        df = store.load_oil()
        if df.empty:
            return jsonify({"error": "油价数据不存在"}), 503
        tail = df.tail(max(limit, 30))
        return jsonify({
            "dates": [d.date().isoformat() for d in tail.index],
            "prices": [float(v) for v in tail["brent_close"]],
        })

    @app.get("/api/meanrev")
    def api_meanrev():
        """当前时点极端均值回复信号(严格无前视)。触发条件:偏离20日均线>=2x滚动波动率。"""
        from app.models.mean_reversion import mean_rev_signal
        df = store.load_rates()
        if df.empty or len(df) < 60:
            return jsonify({"error": "数据不足"}), 503
        close = df["cny_rub"].to_numpy(dtype=float)
        i = len(close) - 1
        # 2026-09-13 Nominal 修复后干净数据重测(k=2.0, 触发日口径)。
        # 注意: 42 个触发日去重后仅 8 个独立危机事件(首日口径 6/8=75%),
        # 样本重叠严重, 数字仅供参考, 不可当稳定胜率。
        hist_prec = {7: 0.619, 30: 0.714, 60: 0.857, 90: 0.810}
        horizons = []
        any_trigger = False
        for N in config.N_HORIZONS:
            sig = mean_rev_signal(close, i, N, k=2.0)
            if sig.get("trigger"):
                any_trigger = True
            horizons.append({
                "N": N,
                "trigger": bool(sig.get("trigger")),
                "direction": (None if sig.get("pred") is None
                              else ("up" if sig["pred"] == 1 else "down")),
                "dev": sig.get("dev"),
                "strength": round(sig.get("strength", 0.0), 2),
                "hist_precision": hist_prec.get(N),
                "reliable": N in (60, 90),
            })
        return jsonify({
            "as_of": df.index[-1].isoformat(),
            "base_rate": round(float(close[i]), 4),
            "any_trigger": any_trigger,
            "threshold_sigma": 2.0,
            "horizons": horizons,
            "note": "极端均值回复:价格远高于20日均线预期回落(跌),远低于预期回升(涨)。"
                    "干净数据触发日口径 60日86%/90日81%, 但2010年至今仅8个独立危机事件"
                    "(首日口径6/8), 覆盖率约1%, 属稀有事件信号, 不代表日常胜率。",
        })

    @app.get("/api/signal_health")
    def api_signal_health():
        """系统健康度: 当前 MOEX 信号滚动命中率(受政策/机制影响的有效程度)。"""
        try:
            from app.monitor_signal import evaluate
            r = evaluate()
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 503
        # 纯前瞻留档成绩单(最近20/50/100次已兑现), 含影子策略 OOS 对照
        try:
            from app.models.prediction_ledger import review
            r["ledger"] = review()
        except Exception as e:  # noqa: BLE001
            r["ledger_error"] = str(e)
        return jsonify(r)

    @app.get("/api/predict")
    def api_predict():
        """面向普通用户的预测数据: 历史汇率 + 未来预测线。
        返回: {hist:[{date,rate}], forecast:[{date,rate}], direction, model_acc, uncertainty}"""
        import json
        n = request.args.get("n", default=7, type=int)
        if n not in config.N_HORIZONS:
            n = 7
        df = store.load_rates()
        if df.empty:
            return jsonify({"error": "无数据"}), 503
        # 历史(按当前 horizon 所需跨度截断, 前端最多只用这么多)
        span = max(n * 5, 60)
        tail = df.tail(span)
        hist = [{"date": d.date().isoformat(), "rate": float(v)}
                for d, v in zip(tail.index, tail["cny_rub"])]
        # ---- 预测投影: 直接用 JSON 里的期限结构拼接曲线 ----
        # (长周期图逐段服从 7/30/60 日更短模型; 拼接逻辑在 save_forecasts)
        fc = load_forecast(n)
        forecast = []
        direction = None
        if fc:
            direction = fc.get("direction")
            forecast = fc.get("forecast", [])
        elif config.N_HORIZONS:
            # JSON 缺失(首次启动尚未跑过预测)时单段兜底
            cur = float(df["cny_rub"].iloc[-1])
            import datetime as _dt
            lastd = _dt.date.fromisoformat(df.index[-1].date().isoformat())
            dates = [(lastd + _dt.timedelta(days=k + 1)).isoformat()
                     for k in range(n)]
            from app.forecast import recent_daily_vol
            import numpy as _np
            daily_vol = recent_daily_vol(
                _np.log(df["cny_rub"].to_numpy(dtype=float)))
            forecast = build_projection(cur, None, dates,
                                        daily_vol=daily_vol)
        # 历史成绩单: 7 日用 MOEX 方向回测;30/60/90 日用长周期模型自己的 OOS 回测
        # (严禁拿 A 模型的命中率给 B 模型背书)。长周期全部信号名都要走这边,
        # 否则会漏进下方 7 日分支拿到旧版回测的错数字。
        # 统一口径: overall=全史命中率(主行), rolling=近一年滚动命中率(副行,
        # 与 /api/signal_health 同算法同数字)。
        model_acc = None
        ev = None
        cutoff = df.index[-1] - pd.Timedelta(days=WINDOW_DAYS)
        if direction and direction.get("signal") in (
                "long_reversion", "moex_spread", "extreme_dist", "breakout") \
                and config.LONGHORIZON_JSON.exists():
            try:
                with open(config.LONGHORIZON_JSON, encoding="utf-8") as f:
                    lj = json.load(f)
                lh = lj.get("horizons", {}).get(str(n), {})
                model_acc = {"overall": lh.get("oos_hit")}
            except (OSError, json.JSONDecodeError):
                model_acc = None
            if model_acc is not None:
                try:
                    ev = _longhorizon_preds(df, n, oil_df=store.load_oil())
                except Exception:
                    ev = None   # 滚动命中率算不出不阻塞预测卡
        elif config.DIRECTION_JSON.exists():
            try:
                with open(config.DIRECTION_JSON, encoding="utf-8") as f:
                    dj = json.load(f)
            except (OSError, json.JSONDecodeError):
                dj = {}   # 文件正在被调度器重写: 本轮拿不到准确率, 下次刷新再取
            h = dj.get("horizons", {}).get(str(n), {})
            model_acc = {"overall": h.get("moex_accuracy", h.get("accuracy"))}
            try:
                ev = _moex7_preds(df)
            except Exception:
                ev = None
        if model_acc is not None and ev is not None:
            recent = [hit for d, hit in ev if pd.Timestamp(d) >= cutoff]
            if recent:
                model_acc["rolling"] = round(sum(recent) / len(recent), 4)
                model_acc["rolling_n"] = len(recent)

        # ---- 不确定性上下文(解释为什么这个预测可能不准) ----
        uncertainty = _build_uncertainty(df, forecast, direction, n)
        # 期限结构图注: 更短周期模型与本周期方向不一致时, 解释曲线为何转折
        if fc and n > 7 and uncertainty.get("points") is not None:
            anchors = fc.get("term_anchors") or []
            final_pred = direction.get("prediction") if direction else None
            for a in anchors:
                if a["horizon"] < n and a.get("prediction") in (0, 1) \
                        and final_pred in (0, 1) \
                        and a["prediction"] != final_pred:
                    sd, fd = ("涨", "跌") if a["prediction"] == 1 else ("跌", "涨")
                    uncertainty["points"].append(
                        f"图中前{a['horizon']}天跟随{a['horizon']}日模型（看{sd}），"
                        f"其后转向本{n}日模型（看{fd}）——中位曲线为多周期逐段拼接，"
                        f"不是单一方向直线；转折点反映的正是不同期限观点的分歧。")
                    break
        # 校准新鲜度: 过期时把握度已静默回退到内置默认表, 必须让界面能显示出来
        from app.models.moex_dir import calibration_status

        return jsonify({
            "as_of": df.index[-1].date().isoformat(),
            "current_rate": float(df["cny_rub"].iloc[-1]),
            "n": n,
            "hist": hist,
            "forecast": forecast,
            "direction": direction,
            "model_acc": model_acc,
            "uncertainty": uncertainty,
            "calibration": calibration_status(),
        })

    @app.get("/api/health")
    def api_health():
        df = store.load_rates()
        return jsonify({
            "db_rows": len(df),
            "db_last_date": df.index[-1].isoformat() if len(df) else None,
            "official_daily_asof": _official_asof(),
            # 安装目录身份: start 脚本据此判断 8000 上的服务是否来自本文件夹
            # (多版本文件夹并存时, 桌宠必须连本文件夹的服务, 否则读到冻结的旧预测)
            "app_root": str(config.ROOT),
        })

    return app


def _weak_uncertainty(direction: dict, n: int) -> dict:
    """长周期实验档文案: 给方向但全程标注未达 70% 验证, 禁止按高把握展示。"""
    rev = direction.get("rev")
    conf = direction["confidence"]
    modern = direction.get("modern_hit")
    emitted = direction.get("rule_emitted")
    bn = direction.get("bucket_n")
    br = direction.get("bucket_rate")
    d = "涨（超跌反弹倾向）" if direction["prediction"] == 1 else "跌（涨多回落倾向）"
    modern_txt = (f"；但 2021 年至今仅 {modern*100:.0f}%，与抛硬币相差不大"
                  if modern is not None else "")
    bucket_txt = (f"；当前形态所在桶历史 {bn} 次、兑现 {br*100:.0f}%"
                  if br is not None and bn else "")
    pool_txt = "（含 MOEX 市场价同向确认）" if direction.get("confirmed") else ""
    return {
        "level": "weak",
        "title": f"弱信号 · 历史 {conf*100:.0f}%",
        "strength": 0.35,
        "signal": "long_reversion",
        "points": [
            f"价格相对 120 日中枢偏离 rev={rev:+.2f}，"
            f"{n} 日均值回复规则给出看涨/看跌的倾向，但把握度低于 55%，按弱信号展示。",
            f"证据等级：该规则历史上共开口 {emitted} 次、"
            f"整体兑现 {conf*100:.0f}%{bucket_txt}{pool_txt}{modern_txt}。",
            "使用限制：弱信号不构成换汇操作依据，不要据此赌单边；"
            "下图中位线刻意不偏移，只有 80% 波动区间可用于预算。",
            "升级机制：数据每日复核，把握度回升至 55% 以上将自动恢复明确涨跌信号。",
        ],
        "disclaimer": "把握度低于 55%，信号可靠性有限；不构成投资建议。",
        "badge": {"label": "把握有限", "tier": "low"},
    }


def _long_uncertainty(direction: dict, n: int) -> dict:
    """30/60/90 日模型的解释文案。弱信号口径: 把握度 < 55% 走弱档文案,
    其余走正常涨跌文案(与前端 hero 三态判定一致)。"""
    rev = direction.get("rev")
    z = direction.get("z")
    conf = direction.get("confidence")
    weak = isinstance(conf, (int, float)) and conf < 0.55
    if weak:
        return _weak_uncertainty(direction, n)
    if direction.get("neutral"):
        reason = direction.get("neutral_reason", "")
        if reason == "calibration_unavailable":
            detail = "长周期校准文件缺失或已过期，本周期暂不提供方向判断。"
        elif reason == "no_validated_signal":
            detail = ("该周期尺度经多组候选信号严格回测，无一达到 70% 命中门槛，"
                      "常态不提供方向，仅提供预算区间。")
        elif reason == "weak_regime_off":
            detail = ("30 日弱倾向规则只在市场高波动状态工作（危机后回归效应），"
                      "当前处于低波动状态，规则休眠、无方向可给。")
        elif reason == "below_weak_gate":
            detail = ("当前虽处于高波动状态，但该偏离形态的历史命中率不足 55% "
                      "弱档门槛，30 日不提供方向。")
        elif reason == "no_moex_confirmation":
            detail = ("价格偏离虽极端，但缺少莫斯科交易所市场价同向确认，"
                      "该形态历史证据不足 70% 门槛，不表态。")
        elif reason == "insufficient_history":
            detail = "历史数据不足，无法生成本周期的偏离与校准统计。"
        else:
            detail = ("当前价格相对 120 日中枢偏离极端，但历史同类形态"
                      "在该周期的兑现胜率未达到 70% 的有效性门槛，模型选择不表态。")
        rev_txt = f"{rev:+.2f}" if isinstance(rev, (int, float)) else "—"
        if isinstance(rev, (int, float)):
            ext = ("已达到极端偏离条件"
                   if abs(rev) >= 1.5
                   else f"距极端阈值还差 {1.5 - abs(rev):.2f} 个标准差")
        else:
            ext = "数据不足"
        return {
            "level": "neutral",
            "title": f"未来 {n} 天方向不明 · 涨跌皆有可能",
            "strength": 0.0,
            "signal": "long_reversion",
            "points": [
                detail,
                f"实时监测中：当前价格相对 120 日中枢偏离 rev={rev_txt}，"
                f"极端偏离判定（|rev|≥1.5）{ext}。30 日弱倾向规则仅在高波动状态"
                f"且该形态历史命中率≥55% 时才给方向，平时休眠。",
                "这不是系统故障：长期尺度上多数时候本来就没有可靠方向，"
                "强行预测涨跌等于抛硬币。",
                f"下图区间是历史覆盖率约 80% 的波动范围（已按实测校准，"
                f"非 ±1σ 名义带），按区间上沿留预算余量，不应用于判断涨跌。",
            ],
            "disclaimer": "不构成投资建议；预测基于历史规律，无法保证未来表现。",
            "badge": {"label": "方向不明", "tier": "neutral"},
        }

    d = "涨（超跌反弹）" if direction["prediction"] == 1 else "跌（涨多回落）"
    conf = direction["confidence"]
    emitted = direction.get("rule_emitted")
    confirmed = direction.get("confirmed")
    policy_name = {
        "pure_rev": "均值回复规则",
        "moex_z": "MOEX价差+均值回复规则",
        "extreme_dist": "极值距离规则",
        "breakout": "极值突破规则",
    }.get(direction.get("policy"), "方向规则")
    pts = [
        f"当前价格相对 120 日中枢偏离 {rev:+.1f} 个标准差（rev={rev:+.2f}），"
        f"模型判断未来 {n} 个交易日看{d}。",
        f"把握度依据：{policy_name}在 2021 年至今的扩展窗回测中共发出 "
        f"{emitted} 次方向预测，事后实际兑现 {conf*100:.0f}%。"
        f"把握度 ≥55% 才显示明确涨跌方向，低于 55% 按弱信号处理。",
    ]
    if direction.get("policy") == "moex_z" and confirmed:
        gc = direction.get("gate_confirms")
        if direction.get("gate_passed"):
            pts.append(
                f"当日三重确认闸门——MOEX 5日动量、在岸价差近5日变化、Brent 20日油价"
                f"——有 {gc}/3 项与信号同向（至少 2 项才给方向，z={z:+.2f}）。")
        else:
            pts.append(
                f"当日三重确认仅 {gc}/3 项同向（不足 2 项），此方向按“确认不足”形态的"
                f"历史兑现率估计把握度（z={z:+.2f}），证据偏弱。")
    elif confirmed:
        pts.append(f"莫斯科交易所(MOEX)市场价同向确认（z={z:+.2f}），"
                   "官方价与市场价指向一致。")
    else:
        pts.append("当前无 MOEX 同向确认，证据强度低于确认形态。")
    pts.append("风险：长周期规律可能被央行政策、地缘事件打破；"
               "该形态样本集中在近年市场环境，换汇请分批操作。")
    pts.append("下图区间为历史覆盖率约 80% 的波动范围（含方向偏移示意），"
               "做预算按区间上沿留余量。")
    validated = bool(direction.get("validated_70pct"))
    badge_label = "把握较高" if validated else "中等把握"
    return {
        "level": "high",
        "title": f"预测看{ '涨' if direction['prediction']==1 else '跌'} · 把握 {conf*100:.0f}%",
        "strength": 0.74,
        "signal": direction.get("signal", "long_reversion"),
        "points": pts,
        "disclaimer": "不构成投资建议；预测基于历史规律，无法保证未来表现。",
        "badge": {"label": badge_label, "tier": "high" if validated else "mid"},
    }


def _recent_trend(df) -> str:
    """返回最近 20 个交易日的滑动趋势描述(独立于模型, 供不确定性解释)。"""
    if len(df) < 25:
        return "数据不足"
    close = df["cny_rub"].to_numpy(float)
    seg = close[-20:]
    chg = (seg[-1] / seg[0] - 1) * 100
    # 方向一致性: 最近5日创新高/低
    if chg > 3:
        return f"近20日累计上涨 {chg:.1f}%"
    if chg < -3:
        return f"近20日累计下跌 {chg:.1f}%"
    return f"近20日累计变动 {chg:+.1f}%"


def _build_uncertainty(df, forecast, direction, n) -> dict:
    """生成预测的不确定性标注: 说明当前信号强度 + 为什么可能不准。
    全部根据 direction 已有字段(无新计算) + 近期走势 + 置信度。"""
    # ===== 长周期模型(30/60/90): 用 policy 字段区分, 避免 moex_z 的
    # signal=moex_spread 与 7 日 MOEX 文案串台 =====
    if direction and direction.get("policy") in (
            "pure_rev", "moex_z", "extreme_dist", "breakout",
            "weak_experimental", "resonance_only", "dual_pool"):
        return _long_uncertainty(direction, n)

    if not direction or direction.get("prediction") not in (0, 1):
        return {
            "level": "mid",
            "title": "暂无明确信号",
            "strength": 0.0,
            "signal": "",
            "points": ["模型当前没有足够清晰的信号，预测可靠程度较低，请谨慎参考。"],
            "disclaimer": "不构成投资建议；预测基于历史规律，无法保证未来表现。",
            "badge": {"label": "信号不明", "tier": "mid"},
        }

    pred = direction["prediction"]
    conf = direction.get("confidence", 0.5)
    z = direction.get("z")
    confirms = direction.get("confirms")
    signal = direction.get("signal", "")
    trend = _recent_trend(df)
    az = abs(z) if z is not None else 0
    d = "涨" if pred == 1 else "跌"

    # 按置信率分三档: ≥65 把握较高 / ≥55 中等把握 / <55 把握有限
    if conf >= 0.65:
        tier, lname, strength = "high", "把握较高", 0.74
        title = f"预测看{d} · 把握 {conf * 100:.0f}%"
        pts = [
            f"{trend}，模型判断未来 {n} 日看{d}。",
            f"判断依据：莫斯科交易所(MOEX)在岸交易价与官方牌价出现显著偏离（|z|={az:.1f}）"
            + (f"，另有{confirms}项当日信号同向佐证（不额外调整把握度）。"
               if confirms and confirms >= 2 else "。"),
            f"历史上该置信水平的预测准确率约 {conf * 100:.0f}%，但仍受突发事件、央行政策等不可控因素影响。",
        ]
    elif conf >= 0.55:
        tier, lname, strength = "mid", "中等把握", 0.60
        title = f"预测看{d} · 把握 {conf * 100:.0f}%"
        pts = [
            f"{trend}，模型判断未来 {n} 日看{d}。",
            f"判断依据：MOEX偏离信号中等（|z|={az:.1f}），方向有一定倾向但不够明确。",
            f"建议结合更多外部信息综合判断，不宜作为单一决策依据。",
        ]
    else:
        tier, lname, strength = "low", "把握有限", 0.5
        title = f"预测看{d} · 把握 {conf * 100:.0f}%"
        pts = [
            f"{trend}，模型判断未来 {n} 日看{d}。",
            f"判断依据：当前信号较弱（|z|={az:.1f}），置信度接近随机水平。",
            f"该预测不具备参考价值，建议等待更明确的信号出现后再做判断。",
        ]

    return {
        "level": tier,
        "title": title,
        "strength": round(strength, 3),
        "signal": signal,
        "points": pts,
        "disclaimer": "不构成投资建议；预测基于历史规律，无法保证未来表现。",
        "badge": {"label": lname, "tier": tier},
    }


def _source_counts() -> dict:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) FROM rates GROUP BY source"
        ).fetchall()
    return {r["source"]: r["COUNT(*)"] for r in rows}


_health_cache = {"asof": None, "ts": 0}

def _official_asof():
    import time
    now = time.time()
    if _health_cache["asof"] is not None and now - _health_cache["ts"] < 3600:
        return _health_cache["asof"]
    from app.data import fetcher
    d = fetcher.fetch_daily_asof()
    val = d.isoformat() if d else None
    _health_cache["asof"] = val
    _health_cache["ts"] = now
    return val
