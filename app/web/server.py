"""Flask 静态页 + JSON API。所有分析产物由 cli 生成后由本服务读取。"""
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from app import config
from app.forecast import load_forecast, build_projection
from app.data import store


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
        hist_prec = {7: 0.647, 30: 0.777, 60: 0.777, 90: 0.669}
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
                "reliable": N in (30, 60),
            })
        return jsonify({
            "as_of": df.index[-1].isoformat(),
            "base_rate": round(float(close[i]), 4),
            "any_trigger": any_trigger,
            "threshold_sigma": 2.0,
            "horizons": horizons,
            "note": "极端均值回复:价格远高于20日均线预期回落(跌),远低于预期回升(涨);仅30/60日子集精度可宣称(约77.7%),覆盖率约4%。",
        })

    @app.get("/api/signal_health")
    def api_signal_health():
        """系统健康度: 当前 MOEX 信号滚动命中率(受政策/机制影响的有效程度)。"""
        try:
            from app.monitor_signal import evaluate
            r = evaluate()
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 503
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
        # ---- 预测投影: 以方向模型为准。 ----
        fc = load_forecast(n)
        forecast = []
        direction = None
        if fc:
            direction = fc.get("direction")
        if direction and direction.get("prediction") in (0, 1):
            cur = float(df["cny_rub"].iloc[-1])
            dates = fc.get("forecast_dates", []) if fc else []
            if not dates:
                import datetime as _dt
                lastd = _dt.date.fromisoformat(df.index[-1].date().isoformat())
                dates = [(lastd + _dt.timedelta(days=k + 1)).isoformat()
                         for k in range(n)]
            from app.forecast import recent_daily_vol
            import numpy as _np
            daily_vol = recent_daily_vol(
                _np.log(df["cny_rub"].to_numpy(dtype=float)))
            forecast = build_projection(cur, direction, dates,
                                        daily_vol=daily_vol)
        # 每日预测准确率(滚动, 来自 DIRECTION_JSON 的 MOEX 期或历史回测)
        model_acc = None
        if config.DIRECTION_JSON.exists():
            try:
                with open(config.DIRECTION_JSON, encoding="utf-8") as f:
                    dj = json.load(f)
            except (OSError, json.JSONDecodeError):
                dj = {}   # 文件正在被调度器重写: 本轮拿不到准确率, 下次刷新再取
            h = dj.get("horizons", {}).get(str(n), {})
            model_acc = {
                "overall": h.get("moex_accuracy", h.get("accuracy")),
                "confident": h.get("moex_confident_accuracy", h.get("confident_accuracy")),
            }

        # ---- 不确定性上下文(解释为什么这个预测可能不准) ----
        uncertainty = _build_uncertainty(df, forecast, direction, n)
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
        })

    return app


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
            + (f"，且有{confirms}重信号相互印证。" if confirms and confirms >= 2 else "。"),
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
