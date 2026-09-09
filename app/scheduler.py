"""内置每日自动更新调度器 (标准库 threading, 无外部依赖)。

serve 进程存活期间, 每天到达目标时刻 (默认莫斯科时间 09:00) 自动执行:
  fetch -> backtest -> forecast
产物写盘后 API 实时读取, 无需重启进程。

这是系统自带的更新能力: 只要服务在跑, 汇率变化就会自动反映到
预测 / 准确率 / 喇叭口, 不依赖任何外部调度器。
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app import config

log = logging.getLogger("scheduler")

# 莫斯科时间 UTC+3 (给莫斯科的朋友用, 比北京晚 5 小时)
MSK = timezone(timedelta(hours=3))
UPDATE_HOUR = 9        # 每天更新时刻 (莫斯科时间小时)
UPDATE_MINUTE = 0
CHECK_INTERVAL = 60    # 唤醒检查间隔 (秒)


def _run_update():
    """执行一次完整更新链: fetch -> backtest -> forecast。
    每个子步骤独立 try, 单点失败不阻断后续 (与用户诚实/降级原则一致)。"""
    from app.data import fetcher, store
    from app.data.news import fetch_news
    from app.data.cbr_rates import fetch_key_rate
    from app.data.moex_rates import fetch_moex_onshore

    log.info("[auto-update] start")

    # 1) fetch 各数据源, 子项失败降级为 warning
    try:
        r = fetcher.sync()
        log.info("[auto-update] CBR sync: %s", json.dumps(r, ensure_ascii=False))
    except Exception as e:
        log.warning("[auto-update] CBR sync failed: %s", e)
    try:
        fetcher.fetch_oil_prices()
    except Exception as e:
        log.warning("[auto-update] oil failed: %s", e)
    try:
        fetch_news()
    except Exception as e:
        log.warning("[auto-update] news failed: %s", e)
    try:
        fetch_key_rate()
    except Exception as e:
        log.warning("[auto-update] key_rate failed: %s", e)
    try:
        fetch_moex_onshore()
    except Exception as e:
        log.warning("[auto-update] moex failed: %s", e)

    # 2) backtest 重算准确率 + 校准
    try:
        from app.models.moex_dir import run_direction_backtest, calibrate_moex_z
        from app.cli import _load_all
        df, oil_df, sent_df, rate_df = _load_all()
        dir_rep = run_direction_backtest(df, oil_df=oil_df, sentiment_df=sent_df, rate_df=rate_df)
        with open(config.DIRECTION_JSON, "w", encoding="utf-8") as f:
            json.dump(dir_rep, f, ensure_ascii=False, indent=1)
        log.info("[auto-update] backtest done")
        # 2b) 动态校准 MOEX z 分档置信度
        try:
            calibrate_moex_z(df, oil_df, sent_df, rate_df)
            log.info("[auto-update] calibration done")
        except Exception as e:
            log.warning("[auto-update] calibration failed: %s", e)
    except Exception as e:
        log.warning("[auto-update] backtest failed: %s", e)

    # 3) forecast 重算预测
    try:
        from app.cli import cmd_forecast
        cmd_forecast(None)
        log.info("[auto-update] forecast done")
    except Exception as e:
        log.warning("[auto-update] forecast failed: %s", e)

    log.info("[auto-update] finished")


def _next_run_at(now):
    """返回下一次 09:00 (莫斯科时间) 的 datetime。"""
    target = now.replace(hour=UPDATE_HOUR, minute=UPDATE_MINUTE, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target


MAX_RETRIES = 3


def _loop():
    last_run_date = None
    retry_count = 0
    log.info("[auto-update] scheduler thread started, daily at %02d:%02d MSK",
             UPDATE_HOUR, UPDATE_MINUTE)
    while True:
        try:
            now = datetime.now(MSK)
            if (now.hour == UPDATE_HOUR and now.minute >= UPDATE_MINUTE
                    and last_run_date != now.date()):
                try:
                    _run_update()
                    last_run_date = now.date()
                    retry_count = 0
                except Exception as e:
                    retry_count += 1
                    if retry_count < MAX_RETRIES:
                        log.warning("[auto-update] failed (retry %d/%d): %s",
                                    retry_count, MAX_RETRIES, e)
                    else:
                        log.warning("[auto-update] gave up after %d retries: %s",
                                    MAX_RETRIES, e)
                        last_run_date = now.date()
                        retry_count = 0
        except Exception as e:
            log.warning("[auto-update] loop error: %s", e)
        time.sleep(CHECK_INTERVAL)


def start():
    """启动后台守护调度线程。serve 进程退出时线程随之结束。"""
    t = threading.Thread(target=_loop, name="daily-auto-update", daemon=True)
    t.start()
    return t
