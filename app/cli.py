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
    return _load_df(), _load_oil(), _load_sentiment(), _load_rate_df()


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
    df, oil_df, sent_df, rate_df = _load_all()
    t0 = time.time()
    dir_rep = run_direction_backtest(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
    with open(config.DIRECTION_JSON, "w", encoding="utf-8") as f:
        json.dump(dir_rep, f, ensure_ascii=False, indent=1)
    for N in config.N_HORIZONS:
        h = dir_rep["horizons"].get(str(N))
        if not h: continue
        ca = h.get("confident_accuracy", 0); cn = h.get("confident_windows", 0)
        print(f"N={N}: 全部{h['accuracy']*100:.1f}% 有信心>60% {ca*100:.1f}%({cn}/{h['windows']})")
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


def cmd_serve(args):
    store.init_db()
    # 启动时始终同步最新数据(不跳过)
    print(json.dumps(fetcher.sync(), ensure_ascii=False))
    print(json.dumps(fetcher.fetch_oil_prices(), ensure_ascii=False))
    from app.data.news import fetch_news; fetch_news()
    from app.data.cbr_rates import fetch_key_rate; fetch_key_rate()
    from app.data.moex_rates import fetch_moex_onshore
    try: fetch_moex_onshore()
    except Exception as e: print("MOEX 抓取跳过:", e)
    # 启动时始终重跑回测+校准+预测(确保数据新鲜)
    from app.models.moex_dir import run_direction_backtest, calibrate_moex_z
    df, oil_df, sent_df, rate_df = _load_all()
    dir_rep = run_direction_backtest(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
    with open(config.DIRECTION_JSON, "w", encoding="utf-8") as f:
        json.dump(dir_rep, f, ensure_ascii=False, indent=1)
    calibrate_moex_z(df, oil_df, sent_df, rate_df)
    from app import forecast as fc
    fc.save_forecasts(df, dir_rep, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
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


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch"); sub.add_parser("backtest"); sub.add_parser("forecast")
    sub.add_parser("calibrate")
    sp = sub.add_parser("serve"); sp.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    return {"fetch":cmd_fetch,"backtest":cmd_backtest,"forecast":cmd_forecast,
            "calibrate":cmd_calibrate,"serve":cmd_serve}[args.cmd](args)

if __name__ == "__main__":
    sys.exit(main())
