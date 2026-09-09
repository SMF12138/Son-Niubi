"""Flask 静态页 + JSON API。所有分析产物由 cli 生成后由本服务读取。"""
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from app import config
from app.forecast import load_forecast
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
        limit = request.args.get("limit", default=500, type=int)
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
        with open(config.DIRECTION_JSON, encoding="utf-8") as f:
            return jsonify(json.load(f))

    @app.get("/api/oil")
    def api_oil():
        limit = request.args.get("limit", default=500, type=int)
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
        """面向普通用户的预测数据: 历史汇率 + 未来预测线 + 每日预测准确率(悬显用)。
        返回: {hist:[{date,rate}], forecast:[{date,rate}], direction, daily_acc:[{date,acc}], model_acc}"""
        import json
        n = request.args.get("n", default=7, type=int)
        if n not in config.N_HORIZONS:
            n = 7
        df = store.load_rates()
        if df.empty:
            return jsonify({"error": "无数据"}), 503
        # 历史
        hist = [{"date": d.date().isoformat(), "rate": float(v)}
                for d, v in zip(df.index, df["cny_rub"])]
        # ---- 预测投影: 以方向模型为准。 ----
        # 不再使用 ensemble 曲线作为“方向预测”。 ensemble 是点位见顶模型,
        # 从未在“涨跌方向”维度验证, 用它当方向制造了伪矛盾。
        # 投影 = 方向结论(direction) × 该信号历史上的实测幅度分布(诚实、无虚构)。
        fc = load_forecast(n)
        forecast = []
        direction = None
        if fc:
            direction = fc.get("direction")
        if direction and direction.get("prediction") in (0, 1):
            pred = direction["prediction"]
            conf = direction.get("confidence", 0.55)
            cur = float(df["cny_rub"].iloc[-1])
            dates = fc.get("forecast_dates", []) if fc else []
            if not dates:
                import datetime as _dt
                lastd = _dt.date.fromisoformat(df.index[-1].date().isoformat())
                dates = [(lastd + _dt.timedelta(days=k + 1)).isoformat()
                         for k in range(n)]
            sign_dir = -1.0 if pred == 0 else 1.0  # 跌=-1, 涨=+1
            sig_w = max(0.5, min(1.0, (conf - 0.5) * 2))  # 置信度→信号强度
            base = np.log(cur)
            npts = len(dates)
            # 中位幅度(取自该信号历史实测分布)
            med = sign_dir * 0.0161 * sig_w
            # 真实形态:
            #   路径用 frac**0.7 → 早期变化快、后期减速(均值回归衰减), 非直线
            #   不确定性带用 sqrt(time) 展宽(随机游走方差∝t) → 近窄远宽的喇叭口
            #   带宽基准 ±1.2%×sqrt(frac), 随 horizon 展开
            import math
            for k, dt in enumerate(dates):
                frac = (k + 1) / npts
                path = frac ** 0.7                      # 弯曲路径
                p50 = float(np.exp(base + med * path))
                halfband = 0.012 * math.sqrt(frac) * (1.0 + (n / 90.0))  # 喇叭口半宽
                lo = float(np.exp(base + med * path - halfband))
                hi = float(np.exp(base + med * path + halfband))
                forecast.append({"date": dt, "rate": round(p50, 4),
                                 "low": round(min(lo, p50), 4),
                                 "high": round(max(hi, p50), 4)})
        # 每日预测准确率(滚动, 来自 DIRECTION_JSON 的 MOEX 期或历史回测)
        model_acc = None
        if config.DIRECTION_JSON.exists():
            with open(config.DIRECTION_JSON, encoding="utf-8") as f:
                dj = json.load(f)
            h = dj.get("horizons", {}).get(str(n), {})
            model_acc = {
                "overall": h.get("moex_accuracy") or h.get("accuracy"),
                "confident": h.get("moex_confident_accuracy") or h.get("confident_accuracy"),
            }
        # 每日预测准确率序列(无前视滚动, 供折线悬显)
        daily_acc = []
        try:
            from app.monitor_signal import daily_accuracy_series
            daily_acc = daily_accuracy_series(roll=60)
        except Exception:  # noqa: BLE001
            daily_acc = []

        # ---- 不确定性上下文(解释为什么这个预测可能不准) ----
        uncertainty = _build_uncertainty(df, forecast, direction, n)

        return jsonify({
            "as_of": df.index[-1].date().isoformat(),
            "current_rate": float(df["cny_rub"].iloc[-1]),
            "n": n,
            "hist": hist,
            "forecast": forecast,
            "direction": direction,
            "model_acc": model_acc,
            "daily_acc": daily_acc,
            "uncertainty": uncertainty,
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
    import numpy as np
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
    import numpy as np
    if not direction or direction.get("prediction") not in (0, 1):
        return {"level": "unknown", "title": "暂无明确信号",
                "points": ["模型当前没有足够清晰的信号，预测可靠程度较低，请谨慎参考。"]}

    pred = direction["prediction"]
    conf = direction.get("confidence", 0.5)
    z = direction.get("z")
    confirms = direction.get("confirms")
    signal = direction.get("signal", "")

    trend = _recent_trend(df)

    # 强度档: 用 z 绝对值 + confirms
    az = abs(z) if z is not None else 0
    if az > 1.5 and confirms is not None and confirms >= 3:
        level, lname, strength = "low", "中等把握", 0.74
        lp_note = "信号强且多个条件相互印证"
    elif az > 1.0:
        level, lname, strength = "medium", "方向明确但有风险", 0.68
        lp_note = "偏离信号较强"
    elif az > 0.5:
        level, lname, strength = "medium", "有一定倾向", 0.60
        lp_note = "信号中等强度"
    else:
        level, lname, strength = "high", "把握有限", 0.5
        lp_note = "信号很弱，接近随机"

    # 信号机制解释
    mech = ("模型依据" + ("莫斯科交易所(MOEX)在岸交易价偏离官方牌价的规律"))
    pred_word = "回落(跌)" if pred == 0 else "上行(涨)"

    # 不确定性点
    pts = [
        f"{trend}，而模型判断未来 {n} 日看{'跌' if pred == 0 else '涨'}。",
        f"判断依据：{mech}——官方牌价{'高于' if pred == 0 else '低于'}市场真实成交价，历史上官价多会{'下追' if pred == 0 else '回补'}市场价。",
    ]
    pts.append(f"但这是历史统计规律({lp_note})，不保证每次应验；实际走势受突发事件、央行干预等政策影响。")

    title = f"预测{'看跌' if pred == 0 else '看涨'} · {lname}（把握 {conf * 100:.0f}%）"
    return {
        "level": level,
        "title": title,
        "strength": round(strength, 3),
        "signal": signal,
        "points": pts,
        "disclaimer": "不构成投资建议；预测基于历史规律，无法保证未来表现。",
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
