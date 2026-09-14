"""内置双层调度器:快层(分钟级数据+预测) + 慢层(日级回测+校准)。

serve 进程存活期间:
  快层: 每 FAST_INTERVAL 秒拉取数据 + 重算预测(约3秒)
  慢层: 每天 09:00 MSK 跑完整 回测+校准+预测(约8秒)

产品写盘后 API 实时读取, 无需重启进程。
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app import config
from app.data import store

log = logging.getLogger("scheduler")

MSK = timezone(timedelta(hours=3))
UPDATE_HOUR = 9
UPDATE_MINUTE = 0
FAST_INTERVAL = 60     # 快层间隔(秒), 可改为更小值
SLOW_INTERVAL = 60     # 慢层检查间隔(秒)
SLOW_RETRY_INTERVAL = 1800  # 慢层失败后的重试节流(秒): 不放弃当天, 每30分钟再试
MAX_RETRIES = 3        # 仅日志分级用, 不再"重试3次放弃当天"


def _fetch_data():
    """快层: 只拉数据, 不做回测/校准。"""
    from app.data import fetcher
    from app.data.cbr_rates import fetch_key_rate
    from app.data.moex_rates import fetch_moex_onshore
    from app.data.moex_live import write_live

    # 新闻情绪已于 2026-09 下线(对预测贡献逐位为 0), 不再自动抓取 Google RSS。
    for name, fn in [("CBR", lambda: fetcher.sync()),
                     ("oil", fetcher.fetch_oil_prices),
                     ("key_rate", fetch_key_rate),
                     ("moex", fetch_moex_onshore),
                     ("moex_live", write_live)]:
        try:
            fn()
        except Exception as e:
            log.warning("[fast] %s failed: %s", name, e)


def _run_forecast_only():
    """快层: 用最新数据重算预测(不重跑回测)。"""
    from app.cli import _load_all
    from app import forecast as fc

    df, oil_df, sent_df, rate_df = _load_all()
    fc.save_forecasts(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)


def _run_full_update():
    """慢层: 完整链路 fetch + backtest + calibrate + forecast。"""
    from app.data import fetcher
    from app.data.cbr_rates import fetch_key_rate
    from app.data.moex_rates import fetch_moex_onshore

    log.info("[slow] full update start")

    # 新闻情绪已下线(贡献为 0), 慢层不再抓取。
    for name, fn in [("CBR", lambda: fetcher.sync()),
                     ("oil", fetcher.fetch_oil_prices),
                     ("key_rate", fetch_key_rate),
                     ("moex", fetch_moex_onshore)]:
        try:
            fn()
        except Exception as e:
            log.warning("[slow] %s failed: %s", name, e)

    try:
        from app.models.moex_dir import run_direction_backtest, calibrate_moex_z
        from app.models.longhorizon import run_longhorizon_backtest
        from app.data.moex_rates import load_moex
        from app.cli import _load_all
        df, oil_df, sent_df, rate_df = _load_all()
        dir_rep = run_direction_backtest(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
        store.write_json_atomic(config.DIRECTION_JSON, dir_rep)
        store.write_json_atomic(config.LONGHORIZON_JSON,
                                run_longhorizon_backtest(df, load_moex(), oil_df=oil_df))
        log.info("[slow] backtest done")
        try:
            calibrate_moex_z(df, oil_df, sent_df, rate_df)
            log.info("[slow] calibration done")
        except Exception as e:
            log.warning("[slow] calibration failed: %s", e)
    except Exception as e:
        log.warning("[slow] backtest failed: %s", e)

    try:
        from app import forecast as fc
        from app.cli import _load_all
        df, oil_df, sent_df, rate_df = _load_all()
        fc.save_forecasts(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
        log.info("[slow] forecast done")
    except Exception as e:
        log.warning("[slow] forecast failed: %s", e)

    log.info("[slow] full update finished")


_manual_done_date = None   # serve 启动时手动跑过全链路的日期(MSK), 当天慢层跳过


def mark_slow_done_today():
    """供 cli.serve 调用: 启动时已完成 fetch+回测+校准+预测, 当天不再重复慢层。"""
    global _manual_done_date
    _manual_done_date = datetime.now(MSK).date()
    log.info("[scheduler] serve 启动已完成全链路, 当天慢层跳过: %s", _manual_done_date)


def _loop():
    last_slow_date = _manual_done_date
    retry_count = 0
    last_fast_time = 0
    last_slow_attempt = 0.0   # 上次慢层尝试(含失败), 用于失败时节流重试
    log.info("[scheduler] started: fast=%ds, slow=daily@%02d:%02d MSK",
             FAST_INTERVAL, UPDATE_HOUR, UPDATE_MINUTE)
    while True:
        try:
            now = time.time()
            now_dt = datetime.now(MSK)

            # 快层: 每 FAST_INTERVAL 秒拉数据+重算预测
            if now - last_fast_time >= FAST_INTERVAL:
                try:
                    _fetch_data()
                    _run_forecast_only()
                    last_fast_time = now
                except Exception as e:
                    log.warning("[fast] update failed: %s", e)

            # 慢层: 每天 09:00 MSK 及之后跑完整回测+校准。
            # 用 >= 而不是 == 是为了「补跑」: 机器在 09:00 那一小时没开着时, 当天稍后启动或运行
            # 仍会补上; 否则会一路跳过到次日, 校准文件超过 48h 后被静默回退到内置默认表。
            # 失败不放弃当天: 每 SLOW_RETRY_INTERVAL 秒重试, 直到成功 —— 保证校准不过期。
            if ((now_dt.hour, now_dt.minute) >= (UPDATE_HOUR, UPDATE_MINUTE)
                    and last_slow_date != now_dt.date()
                    and now - last_slow_attempt >= SLOW_RETRY_INTERVAL):
                last_slow_attempt = now
                try:
                    _run_full_update()
                    last_slow_date = now_dt.date()
                    retry_count = 0
                except Exception as e:
                    retry_count += 1
                    if retry_count <= MAX_RETRIES:
                        log.warning("[slow] failed (retry %d): %s",
                                    retry_count, e)
                    else:
                        # 持续失败: 降级日志频率, 但仍按节流间隔重试到当天成功
                        log.warning("[slow] still failing after %d attempts, "
                                    "next retry in %ds: %s",
                                    retry_count, SLOW_RETRY_INTERVAL, e)

        except BaseException as e:   # 含 SystemExit(cli._load_df 数据不足时抛出), 调度线程必须活着
            log.warning("[scheduler] loop error: %s", e)
        time.sleep(1)


def start():
    t = threading.Thread(target=_loop, name="dual-scheduler", daemon=True)
    t.start()
    return t
