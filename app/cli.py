"""命令入口: fetch | backtest | calibrate | forecast | serve"""
import argparse, json, logging, webbrowser, sys, time
from app import config
from app.data import fetcher, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cli")

def _load_df():
    df = store.load_rates()
    if len(df) < 100: raise SystemExit(f"数据不足({len(df)} 行)")
    return df

def _load_oil():
    store.init_db(); oil = store.load_oil()
    if oil.empty: fetcher.fetch_oil_prices(); oil = store.load_oil()
    return oil

def _load_sentiment():
    store.init_db(); return store.load_daily_sentiment()

def _load_rate_df():
    store.init_db(); rate_df = store.load_key_rate()
    if rate_df.empty:
        from app.data.cbr_rates import fetch_key_rate; fetch_key_rate()
        rate_df = store.load_key_rate()
    return rate_df

def _load_all():
    # 新闻情绪已于 2026-09 从生产链路下线: 有/无情绪完整回测四周期指标逐位相同
    # (C5 确认对方向与把握度贡献均为 0); 自动抓取已停, 若继续读旧表, ffill 会把
    # 最后一个情绪值向未来无限前传污染 confirms 展示, 故生产链路固定传 None。
    return _load_df(), _load_oil(), None, _load_rate_df()


def cmd_fetch(_):
    store.init_db()
    print("CBR:", json.dumps(fetcher.sync(), ensure_ascii=False))
    print("Brent:", json.dumps(fetcher.fetch_oil_prices(), ensure_ascii=False))
    from app.data.news import fetch_news
    print("新闻:", json.dumps(fetch_news(), ensure_ascii=False))
    from app.data.cbr_rates import fetch_key_rate
    print("利率:", json.dumps(fetch_key_rate(), ensure_ascii=False))
    return 0


def cmd_backtest(_):
    from app.models.moex_dir import run_direction_backtest
    from app.models.longhorizon import run_longhorizon_backtest
    from app.data.moex_rates import load_moex
    df, oil_df, sent_df, rate_df = _load_all()
    t0 = time.time()
    dir_rep = run_direction_backtest(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
    store.write_json_atomic(config.DIRECTION_JSON, dir_rep)
    for N in config.N_HORIZONS:
        h = dir_rep["horizons"].get(str(N))
        if not h: continue
        ca = h.get("confident_accuracy", 0); cn = h.get("confident_windows", 0)
        print(f"N={N}: 全部{h['accuracy']*100:.1f}% "
              f"高置信>{config.CONFIDENT_THRESHOLD*100:.0f}% {ca*100:.1f}%({cn}/{h['windows']})")
    lh = run_longhorizon_backtest(df, load_moex(), oil_df=oil_df)
    store.write_json_atomic(config.LONGHORIZON_JSON, lh)
    for N in (30, 60, 90):
        h = lh["horizons"][str(N)]
        hit = "—" if h["oos_hit"] is None else f"{h['oos_hit']*100:.1f}%"
        verdict = "✅过70%门槛" if h["passed_70pct_gate"] else "未过门槛(中性)"
        print(f"长周期 N={N}: OOS命中{hit} 发声{h['oos_emitted']}"
              f"/{h['eligible_days']}(覆盖{h['coverage']*100:.0f}%) -> {verdict}")
    print(f"回测耗时 {time.time()-t0:.0f}s")
    return 0


def cmd_forecast(_):
    from app import forecast as fc
    df, oil_df, sent_df, rate_df = _load_all()
    fc.save_forecasts(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
    for N in config.N_HORIZONS:
        f = fc.load_forecast(N)
        if not f: continue
        dr = f.get("direction", {})
        if dr.get("prediction") not in (0, 1):
            print(f"N={N}: 方向=方向不明(中性,只给区间)")
            continue
        arrow = "↑涨" if dr.get("prediction") == 1 else "↓跌"
        print(f"N={N}: 方向={arrow} 置信度{dr.get('confidence', 0)*100:.0f}%")
    return 0


def cmd_calibrate(_):
    from app.models.moex_dir import calibrate_moex_z
    df, oil_df, sent_df, rate_df = _load_all()
    t0 = time.time()
    result = calibrate_moex_z(df, oil_df, sent_df, rate_df)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"校准耗时 {time.time()-t0:.0f}s")
    return 0


def _port_in_use(host: str, port: int) -> bool:
    """探测端口是否已有监听(单实例保护): 两个 serve 并存会双跑调度器,
    造成重复抓取/回测与 SQLITE_BUSY。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def cmd_serve(args):
    store.init_db()
    if _port_in_use(config.HOST, config.PORT):
        log.warning("http://%s:%s 已有服务在监听, 单实例模式退出"
                    "(避免双调度器重复抓取/回测)", config.HOST, config.PORT)
        return 0
    # 启动时同步最新数据(数据源无更新时 ~2s 内完成, 不拖慢)
    # 任一数据源失败只记日志, 用已有缓存继续 —— 断网也要能开看板
    prev_last = store.last_date()
    from app.data.cbr_rates import fetch_key_rate
    from app.data.moex_rates import fetch_moex_onshore
    # 新闻情绪已下线(贡献为 0), 启动同步不再抓 Google RSS。
    for name, fn in [("CBR", lambda: fetcher.sync()),
                     ("oil", fetcher.fetch_oil_prices),
                     ("key_rate", fetch_key_rate),
                     ("moex", fetch_moex_onshore)]:
        try:
            print(f"{name}:", json.dumps(fn(), ensure_ascii=False))
        except Exception as e:
            log.warning("启动同步 %s 失败(用缓存继续): %s", name, e)
    # 仅在"数据有新增"或"产物缺失"时重跑回测+校准+预测, 否则跳过(秒启动)
    new_last = store.last_date()
    if (new_last != prev_last or not config.DIRECTION_JSON.exists()
            or not config.FORECAST_JSONS.get(7).exists()):
        try:
            from app.models.moex_dir import run_direction_backtest, calibrate_moex_z
            from app.models.longhorizon import run_longhorizon_backtest
            from app.data.moex_rates import load_moex
            df, oil_df, sent_df, rate_df = _load_all()
            dir_rep = run_direction_backtest(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
            store.write_json_atomic(config.DIRECTION_JSON, dir_rep)
            store.write_json_atomic(config.LONGHORIZON_JSON,
                                    run_longhorizon_backtest(df, load_moex(), oil_df=oil_df))
            calibrate_moex_z(df, oil_df, sent_df, rate_df)
            from app import forecast as fc
            fc.save_forecasts(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
            # 已在启动时跑完慢层全链路, 通知调度器当天不要重复
            from app import scheduler
            scheduler.mark_slow_done_today()
        except SystemExit as e:
            # 首次运行且数据源全部抓取失败: 不阻断看板启动, 页面自然显示空数据
            log.warning("历史数据不足, 跳过回测/校准/预测(%s); 看板仍会启动", e)
    else:
        log.info("数据无更新, 跳过回测(产物已最新)")
        # 即使数据没变, 也重算一次预测: 更新 refreshed_at 时间戳,
        # 让前端能看到系统在刷新(而不是误以为"卡在昨天")。
        try:
            from app import forecast as fc
            df, oil_df, sent_df, rate_df = _load_all()
            fc.save_forecasts(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
        except Exception as e:
            log.warning("启动预测刷新失败(用旧产物): %s", e)
    from app.web.server import create_app
    app = create_app()
    # 启动内置每日自动更新调度器: 系统自带更新能力, 不依赖外部调度
    from app import scheduler
    scheduler.start()
    url = f"http://{config.HOST}:{config.PORT}"
    log.info("仪表盘: %s", url)
    if not args.no_browser: webbrowser.open(url)
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)
    return 0


def cmd_widget(_):
    from floating_pet import FloatingPet
    pet = FloatingPet()
    pet.run()
    return 0


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch"); sub.add_parser("backtest"); sub.add_parser("forecast")
    sub.add_parser("calibrate"); sub.add_parser("widget")
    sp = sub.add_parser("serve"); sp.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    return {"fetch":cmd_fetch,"backtest":cmd_backtest,"forecast":cmd_forecast,
            "calibrate":cmd_calibrate,"serve":cmd_serve,"widget":cmd_widget}[args.cmd](args)

if __name__ == "__main__":
    sys.exit(main())
