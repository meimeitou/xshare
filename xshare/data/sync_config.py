"""定时同步任务的配置管理、执行器与后台循环。

职责边界：本模块管"配置读写 + 阻塞执行器 + 窗口判定 + 后台 timer"。
任务执行入口 run_job 与 worker 在 task_queue.py（单向依赖：sync_loop
调 task_queue.enqueue，task_queue 顶部 import 本模块的执行器）。
"""

from __future__ import annotations

import asyncio
import logging
import json
import os
import threading
from datetime import date, datetime, time, timedelta

from xshare.data.sqlite_db import get_sqlite_conn, init_sqlite_tables
from xshare.utils import env_int

logger = logging.getLogger(__name__)

NEWS_JOB = "news"
STOCK_JOB = "stock_basic"
DAILY_JOB = "daily"
INDEX_BASIC_JOB = "index_basic"
INDEX_DAILY_JOB = "index_daily"
ETF_BASIC_JOB = "etf_basic"
FUND_DAILY_JOB = "fund_daily"
TRADE_CAL_JOB = "trade_cal"
DAILY_BASIC_JOB = "daily_basic"
FINANCE_JOB = "finance"
QUOTE_JOB = "quote"
MONEYFLOW_JOB = "moneyflow"
SECTOR_MONEYFLOW_JOB = "sector_moneyflow"
MARKET_MONEYFLOW_JOB = "market_moneyflow"
LIMIT_LIST_JOB = "limit_list"
CONCEPT_BOARD_JOB = "concept_board"
CONCEPT_MEMBER_JOB = "concept_member"
MAINLINE_JOB = "mainline"

ALL_JOBS = (
    NEWS_JOB,
    STOCK_JOB,
    DAILY_JOB,
    INDEX_BASIC_JOB,
    INDEX_DAILY_JOB,
    ETF_BASIC_JOB,
    FUND_DAILY_JOB,
    TRADE_CAL_JOB,
    DAILY_BASIC_JOB,
    FINANCE_JOB,
    MONEYFLOW_JOB,
    SECTOR_MONEYFLOW_JOB,
    MARKET_MONEYFLOW_JOB,
    LIMIT_LIST_JOB,
    CONCEPT_BOARD_JOB,
    CONCEPT_MEMBER_JOB,
    QUOTE_JOB,
    MAINLINE_JOB,
)

# 日历触发（非 interval）的任务：交易日 16:00 各入队一次
CALENDAR_JOBS = frozenset({
    DAILY_JOB, INDEX_DAILY_JOB, FUND_DAILY_JOB, DAILY_BASIC_JOB,
    MONEYFLOW_JOB, SECTOR_MONEYFLOW_JOB, MARKET_MONEYFLOW_JOB,
    LIMIT_LIST_JOB, CONCEPT_BOARD_JOB, CONCEPT_MEMBER_JOB,
})

# 仅交易时段运行的 interval 任务：非交易时段 interval loop 不入队
TRADING_HOURS_JOBS = frozenset({QUOTE_JOB})

# 日历任务的触发时刻（本地时间，交易日 16:00）。
# Tushare 日线通常 15:00-16:00 入库，16:00 触发即可拉取当日收盘数据。
_CALENDAR_TRIGGER_HOUR = 16
_CALENDAR_TRIGGER_MINUTE = 0


JOB_META: dict[str, dict] = {
    NEWS_JOB: {
        "label": "新闻同步",
        "description": "同花顺 7×24 快讯写入 DuckDB news 表",
        "params_schema": {
            "pages": {"type": "integer", "default": 3, "description": "抓取页数"},
            "retain_days": {"type": "integer", "default": 1, "description": "新闻保留天数"},
        },
    },
    STOCK_JOB: {
        "label": "股票列表",
        "description": "Tushare A 股基础信息写入 stock_basic（需 TUSHARE_TOKEN）",
        "params_schema": {},
    },
    DAILY_JOB: {
        "label": "日线行情",
        "description": "全市场日线 OHLCV；交易日 16:00 触发；补数可绕过窗口",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "years": {"type": "integer", "description": "一次性同步最近 N 年历史数据"},
            "backfill": {"type": "boolean", "default": False, "description": "历史补数，忽略 16:00 窗口"},
        },
    },
    INDEX_BASIC_JOB: {
        "label": "指数列表",
        "description": "Tushare 指数基础信息写入 index_basic（默认 SSE/SZSE/CSI）",
        "params_schema": {
            "force": {"type": "boolean", "default": False, "description": "忽略 24h 缓存强制刷新"},
        },
    },
    INDEX_DAILY_JOB: {
        "label": "指数日线",
        "description": "按 index_basic 拉取指数日线；交易日 16:00 触发；补数可绕过窗口",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "years": {"type": "integer", "description": "一次性同步最近 N 年历史数据"},
            "backfill": {"type": "boolean", "default": False, "description": "历史补数，忽略 16:00 窗口"},
        },
    },
    ETF_BASIC_JOB: {
        "label": "ETF 列表",
        "description": "Tushare etf_basic 写入 etf_basic（默认上市状态 L）",
        "params_schema": {
            "force": {"type": "boolean", "default": False, "description": "忽略 24h 缓存强制刷新"},
        },
    },
    FUND_DAILY_JOB: {
        "label": "ETF 日线",
        "description": "Tushare fund_daily 写入 fund_daily；交易日 16:00 触发；补数可绕过窗口",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "years": {"type": "integer", "description": "一次性同步最近 N 年历史数据"},
            "backfill": {"type": "boolean", "default": False, "description": "历史补数，忽略 16:00 窗口"},
        },
    },
    TRADE_CAL_JOB: {
        "label": "交易日历",
        "description": "Tushare 交易日历写入 trade_cal，供本地判定开市日",
        "params_schema": {
            "years": {"type": "integer", "default": 3, "description": "回溯年数"},
        },
    },
    DAILY_BASIC_JOB: {
        "label": "每日指标",
        "description": "全市场 PE/PB 等写入 stock_daily_basic；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
        },
    },
    FINANCE_JOB: {
        "label": "财务指标",
        "description": "按 stock_basic 分片同步 fina_indicator → stock_finance",
        "params_schema": {
            "limit": {"type": "integer", "default": 200, "description": "本次最多同步股票数"},
            "force": {"type": "boolean", "default": False, "description": "忽略 7 天水位"},
        },
    },
    QUOTE_JOB: {
        "label": "行情快照",
        "description": "交易时段每 5 分钟拉取新浪实时行情写入 quote/index/sector_snapshot（无需 TUSHARE_TOKEN）",
        "params_schema": {},
    },
    MONEYFLOW_JOB: {
        "label": "个股资金流向",
        "description": "Tushare moneyflow 写入 stock_moneyflow；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
            "start_date": {"type": "string", "description": "区间补全起始 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "区间补全结束 YYYY-MM-DD"},
            "overwrite": {"type": "boolean", "default": False, "description": "强制重拉覆盖已有数据"},
        },
    },
    SECTOR_MONEYFLOW_JOB: {
        "label": "板块资金流向",
        "description": "Tushare moneyflow_ind_dc 写入 sector_moneyflow（行业/概念/地域）；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
        },
    },
    MARKET_MONEYFLOW_JOB: {
        "label": "大盘资金流向",
        "description": "Tushare moneyflow_mkt_dc 写入 market_moneyflow；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
        },
    },
    LIMIT_LIST_JOB: {
        "label": "涨跌停列表",
        "description": "从 stock_daily + stock_basic 本地计算涨停股及连板数，写入 limit_list（仅 U 类型）；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
        },
    },
    CONCEPT_BOARD_JOB: {
        "label": "概念题材板块",
        "description": "Tushare dc_concept 写入 concept_board（东财概念板块，数据从 2026-02-03 起）；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
        },
    },
    CONCEPT_MEMBER_JOB: {
        "label": "概念题材成分",
        "description": "Tushare dc_concept_cons 写入 concept_member（主线 TOP 概念成分，数据从 2026-02-03 起）；交易日 16:00 触发",
        "params_schema": {
            "days": {"type": "integer", "default": 1, "description": "回溯交易日数"},
            "backfill": {"type": "boolean", "default": False, "description": "忽略 16:00 窗口"},
            "top_n": {"type": "integer", "default": 24, "description": "同步 TOP 概念数（约 sector_top_n*3）"},
        },
    },
    MAINLINE_JOB: {
        "label": "主线方向计算",
        "description": "三维度共振主线分析结果写入 mainline_cache；依赖 concept_board/stock_moneyflow/sector_moneyflow/concept_member 全部就绪后自动入队（limit_list 由 stock_daily 本地计算），不与数据同步并行，不受 16:00 窗口限制",
        "params_schema": {
            "sector_top_n": {"type": "integer", "default": 8, "description": "主线板块数量"},
            "strong_limit": {"type": "integer", "default": 10, "description": "强势股数量"},
        },
    },
}

_POLL_SECONDS = 30
_trade_day_cache: dict[str, bool] = {}
_trade_day_cache_lock = threading.Lock()
_TRADE_DAY_CACHE_MAX = 366 * 5  # 最多缓存 5 年交易日，避免无界增长


def _cache_trade_day(ymd: str, is_open: bool) -> None:
    """线程安全地写入交易日缓存，达到上限时清空重来。"""
    with _trade_day_cache_lock:
        if len(_trade_day_cache) >= _TRADE_DAY_CACHE_MAX:
            _trade_day_cache.clear()
        _trade_day_cache[ymd] = is_open


def _is_trade_day(day: date) -> bool:
    """优先本地 trade_cal → Tushare API → weekday 回退。"""
    ymd = day.strftime("%Y%m%d")
    with _trade_day_cache_lock:
        cached = _trade_day_cache.get(ymd)
    if cached is not None:
        return cached

    try:
        from xshare.data.sources.tushare_source import is_trade_day_local
        local = is_trade_day_local(day)
        if local is not None:
            _cache_trade_day(ymd, local)
            return local
    except Exception as exc:
        logger.debug("本地 trade_cal 查询失败: %s", exc)

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        is_open = day.weekday() < 5
        _cache_trade_day(ymd, is_open)
        return is_open

    try:
        import tushare as ts

        from xshare.data import rate_limit
        rate_limit.acquire("tushare")
        pro = ts.pro_api(token)
        try:
            cal = pro.trade_cal(exchange="", start_date=ymd, end_date=ymd, is_open="1")
        except TypeError:
            cal = pro.trade_cal(start_date=ymd, end_date=ymd)

        if cal is not None and not cal.empty:
            col = "cal_date" if "cal_date" in cal.columns else "trade_date" if "trade_date" in cal.columns else None
            is_open = False
            if col:
                is_open = ymd in set(cal[col].astype(str).tolist())
            _cache_trade_day(ymd, is_open)
            return is_open
    except Exception as exc:
        logger.debug("trade_cal 判定失败，回退 weekday: %s", exc)

    is_open = day.weekday() < 5
    _cache_trade_day(ymd, is_open)
    return is_open


def _daily_sync_window_open(now: datetime | None = None) -> bool:
    """A 股日线/日频同步窗口：交易日 16:00 之后。"""
    current = now or datetime.now()
    if not _is_trade_day(current.date()):
        return False
    return (
        current.hour > _CALENDAR_TRIGGER_HOUR
        or (current.hour == _CALENDAR_TRIGGER_HOUR
            and current.minute >= _CALENDAR_TRIGGER_MINUTE)
    )


def _in_trading_hours(now: datetime | None = None) -> bool:
    """A 股盘中窗口：交易日 09:25-11:35 / 12:55-15:10（含集合竞价与收盘缓冲）。"""
    current = now or datetime.now()
    if not _is_trade_day(current.date()):
        return False
    t = current.time()
    return time(9, 25) <= t <= time(11, 35) or time(12, 55) <= t <= time(15, 10)


# 区间补全 / 覆盖重拉 / 按年拉取：绕过交易日 16:00 窗口
_WINDOW_BYPASS_KEYS = ("backfill", "start_date", "end_date", "overwrite", "years")


def check_calendar_window(
    job: str, payload: dict | None = None, now: datetime | None = None
) -> tuple[bool, str]:
    """检查日历任务是否在可执行窗口内。

    非日历任务、mainline（依赖就绪触发）、补数 payload 总是返回 ``(True, "")``。
    供 ``run_job``（执行前）和 ``sync_loop``（入队前）共用，消除窗口判断重复。

    Returns:
        (eligible, reason) — eligible 为 False 时 reason 说明原因。
    """
    if job not in CALENDAR_JOBS:
        return True, ""
    p = payload or {}
    if any(p.get(k) for k in _WINDOW_BYPASS_KEYS):
        return True, ""
    current = now or datetime.now()
    if not _daily_sync_window_open(current):
        return False, f"{job} 同步仅在交易日 16:00 后执行"
    return True, ""


def _parse_local_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _next_daily_window_open(now: datetime | None = None) -> datetime:
    """下一次日历同步窗口开启时刻（本地时间，交易日 16:00）。"""
    current = now or datetime.now()
    today = current.date()
    if _is_trade_day(today):
        open_at = datetime.combine(today, time(_CALENDAR_TRIGGER_HOUR, _CALENDAR_TRIGGER_MINUTE))
        if current < open_at:
            return open_at
    probe = today
    for _ in range(14):
        probe += timedelta(days=1)
        if _is_trade_day(probe):
            return datetime.combine(probe, time(_CALENDAR_TRIGGER_HOUR, _CALENDAR_TRIGGER_MINUTE))
    return current + timedelta(days=1)


def estimate_next_run_at(job: str, cfg: dict, now: datetime | None = None) -> str | None:
    """估算定时 loop 的下一次入队时间（供前端展示）。"""
    current = now or datetime.now()
    if not cfg.get("enabled"):
        return None

    if job == MAINLINE_JOB:
        # mainline 依赖就绪触发，不受 16:00 窗口限制：每 30s 轮询就绪状态。
        # 若今日已成功则不再预估；否则 30s 后重试。
        last = _parse_local_ts(cfg.get("last_run_at"))
        if last and last.date() == current.date() and cfg.get("last_status") == "ok":
            return None
        return (current + timedelta(seconds=_POLL_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")

    if job in CALENDAR_JOBS:
        if not _daily_sync_window_open(current):
            return _next_daily_window_open(current).strftime("%Y-%m-%d %H:%M:%S")
        # 窗口内：若今日已成功跑过，则指向下一交易日 16:00
        last = _parse_local_ts(cfg.get("last_run_at"))
        if last and last.date() == current.date() and cfg.get("last_status") == "ok":
            return _next_daily_window_open(current + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        return current.strftime("%Y-%m-%d %H:%M:%S")

    interval = int(cfg.get("interval_minutes") or 60)
    base = _parse_local_ts(cfg.get("last_run_at")) or _parse_local_ts(cfg.get("updated_at")) or current
    nxt = base + timedelta(minutes=interval)
    while nxt <= current:
        nxt += timedelta(minutes=interval)
    return nxt.strftime("%Y-%m-%d %H:%M:%S")


def _env_int(name: str, default: int) -> int:
    """读取环境变量为正 int（<=0 回退 default）。

    保留 >0 校验语义（与原实现一致）。
    """
    v = env_int(name, default)
    return v if v > 0 else default


# ─── 配置读写 ────────────────────────────────────────────────────────────────────────

def init_sync_config() -> None:
    """建表 + 种子默认值（幂等）。"""
    init_sqlite_tables()
    conn = get_sqlite_conn()
    seeds = [
        (NEWS_JOB, _env_int("XSHARE_NEWS_SYNC_INTERVAL", 15)),
        (STOCK_JOB, _env_int("XSHARE_STOCK_SYNC_INTERVAL", 1440)),
        (DAILY_JOB, _env_int("XSHARE_DAILY_SYNC_INTERVAL", 1440)),  # 日历任务：间隔仅作展示兜底
        (INDEX_BASIC_JOB, _env_int("XSHARE_INDEX_BASIC_SYNC_INTERVAL", 1440)),
        (INDEX_DAILY_JOB, _env_int("XSHARE_INDEX_DAILY_SYNC_INTERVAL", 1440)),
        (ETF_BASIC_JOB, _env_int("XSHARE_ETF_BASIC_SYNC_INTERVAL", 1440)),
        (FUND_DAILY_JOB, _env_int("XSHARE_FUND_DAILY_SYNC_INTERVAL", 1440)),
        (TRADE_CAL_JOB, _env_int("XSHARE_TRADE_CAL_SYNC_INTERVAL", 10080)),  # 每周
        (DAILY_BASIC_JOB, _env_int("XSHARE_DAILY_BASIC_SYNC_INTERVAL", 1440)),
        (FINANCE_JOB, _env_int("XSHARE_FINANCE_SYNC_INTERVAL", 10080)),
        (MONEYFLOW_JOB, _env_int("XSHARE_MONEYFLOW_SYNC_INTERVAL", 1440)),
        (SECTOR_MONEYFLOW_JOB, _env_int("XSHARE_SECTOR_MONEYFLOW_SYNC_INTERVAL", 1440)),
        (MARKET_MONEYFLOW_JOB, _env_int("XSHARE_MARKET_MONEYFLOW_SYNC_INTERVAL", 1440)),
        (LIMIT_LIST_JOB, _env_int("XSHARE_LIMIT_LIST_SYNC_INTERVAL", 1440)),
        (CONCEPT_BOARD_JOB, _env_int("XSHARE_CONCEPT_BOARD_SYNC_INTERVAL", 1440)),
        (CONCEPT_MEMBER_JOB, _env_int("XSHARE_CONCEPT_MEMBER_SYNC_INTERVAL", 1440)),
        (QUOTE_JOB, _env_int("XSHARE_QUOTE_SYNC_INTERVAL", 5)),
        (MAINLINE_JOB, _env_int("XSHARE_MAINLINE_SYNC_INTERVAL", 1440)),
    ]
    for job, interval in seeds:
        conn.execute(
            """
            INSERT INTO sync_config (job, enabled, interval_minutes, last_run_at, last_status, last_error)
            VALUES (?, 1, ?, NULL, NULL, NULL)
            ON CONFLICT (job) DO NOTHING
            """,
            [job, interval],
        )
    conn.execute("DELETE FROM sync_config WHERE job = ?", ["fund_nav"])
    conn.execute(
        "UPDATE sync_task_queue SET status='cancelled', finished_at=current_timestamp, "
        "last_error='job removed' WHERE task_type='fund_nav' AND status IN ('queued','running')"
    )


def get_one(job: str) -> dict | None:
    conn = get_sqlite_conn()
    row = conn.execute(
        "SELECT job, enabled, interval_minutes, last_run_at, last_status, last_error, updated_at "
        "FROM sync_config WHERE job = ?",
        [job],
    ).fetchone()
    if not row:
        return None
    job_dict = _row_to_dict(row)
    meta = JOB_META.get(job_dict["job"], {})
    job_dict["label"] = meta.get("label", job_dict["job"])
    job_dict["description"] = meta.get("description", "")
    job_dict["params_schema"] = meta.get("params_schema", {})
    job_dict["schedule"] = "calendar_1600" if job_dict["job"] in CALENDAR_JOBS else "interval"
    job_dict["next_run_at"] = estimate_next_run_at(job_dict["job"], job_dict)
    return job_dict


def get_all() -> list[dict]:
    conn = get_sqlite_conn()
    rows = conn.execute(
        "SELECT job, enabled, interval_minutes, last_run_at, last_status, last_error, updated_at "
        "FROM sync_config ORDER BY job"
    ).fetchall()
    jobs = [_row_to_dict(r) for r in rows]
    now = datetime.now()
    for j in jobs:
        meta = JOB_META.get(j["job"], {})
        j["label"] = meta.get("label", j["job"])
        j["description"] = meta.get("description", "")
        j["params_schema"] = meta.get("params_schema", {})
        j["schedule"] = "calendar_1600" if j["job"] in CALENDAR_JOBS else "interval"
        j["next_run_at"] = estimate_next_run_at(j["job"], j, now)
    return jobs


def _row_to_dict(row) -> dict:
    return {
        "job": row[0],
        "enabled": bool(row[1]),
        "interval_minutes": row[2],
        "last_run_at": str(row[3]) if row[3] else None,
        "last_status": row[4],
        "last_error": row[5],
        "updated_at": str(row[6]) if row[6] else None,
    }


def update(job: str, enabled: bool | None = None, interval_minutes: int | None = None) -> dict | None:
    """运行时修改配置，热生效（loop 在 30s 内感知）。"""
    conn = get_sqlite_conn()
    sets: list[str] = []
    params: list = []
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(bool(enabled))
    if interval_minutes is not None:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes 必须为正整数")
        sets.append("interval_minutes = ?")
        params.append(int(interval_minutes))
    if not sets:
        return get_one(job)
    sets.append("updated_at = current_timestamp")
    params.append(job)
    conn.execute(f"UPDATE sync_config SET {', '.join(sets)} WHERE job = ?", params)
    return get_one(job)


def _set_state(job: str, status: str, error: str | None = None) -> None:
    conn = get_sqlite_conn()
    conn.execute(
        "UPDATE sync_config SET last_run_at = current_timestamp, last_status = ?, last_error = ?, updated_at = current_timestamp WHERE job = ?",
        [status, error, job],
    )


# ─── 执行器 ──────────────────────────────────────────────────────────────────

def _sync_news_blocking(payload: dict | None = None) -> int:
    from xshare.data.news import cleanup_old_news, save_news
    from xshare.data.sources.ths_news import fetch_all_pages
    from xshare.data import watermark as wm

    pages = int((payload or {}).get("pages") or _env_int("XSHARE_NEWS_PAGES", 3))
    retain_days = int((payload or {}).get("retain_days") or _env_int("XSHARE_NEWS_RETAIN_DAYS", 1))
    records = fetch_all_pages(max_pages=pages)
    if records:
        save_news(records)
    cleanup_old_news(retain_days=retain_days)
    wm.set_watermark(wm.DATASET_NEWS, "ALL", wm.STATUS_OK, len(records))
    logger.info(
        "[sync] news 完成 table=news rows=%d pages=%d retain_days=%d source=ths_realtime",
        len(records), pages, retain_days,
    )
    return len(records)


def _sync_stock_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_stock_basic_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.debug("未配置 TUSHARE_TOKEN，跳过股票列表同步")
        return 0
    force = bool((payload or {}).get("force"))
    count = sync_stock_basic_to_db(force=force)
    if count:
        logger.info("股票列表已同步: %d 条", count)
    else:
        logger.info("股票列表已是最新，跳过同步")
    return count


def _sync_daily_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_stock_daily_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.debug("未配置 TUSHARE_TOKEN，跳过日线行情同步")
        return 0
    p = payload or {}
    start_date = p.get("start_date")
    end_date = p.get("end_date")
    overwrite = bool(p.get("overwrite"))
    if start_date or end_date:
        count = sync_stock_daily_to_db(
            start_date=start_date, end_date=end_date,
            code=p.get("code"), overwrite=overwrite,
        )
        days = int(p.get("days") or 1)
        if count:
            logger.info("日线行情已同步: %d 条（区间 %s..%s overwrite=%s）",
                        count, start_date, end_date, overwrite)
        else:
            logger.info("日线行情无可同步数据（可能非交易日）")
    else:
        if p.get("years"):
            days = int(p["years"]) * 366
        elif p.get("backfill"):
            days = int(p.get("days") or _env_int("XSHARE_DAILY_BACKFILL_DAYS", 252))
        else:
            days = int(p.get("days") or _env_int("XSHARE_DAILY_SYNC_DAYS", 1))
        count = sync_stock_daily_to_db(days=days, code=p.get("code"), overwrite=overwrite)
        if count:
            logger.info("日线行情已同步: %d 条（最近 %d 个交易日）", count, days)
        else:
            logger.info("日线行情无可同步数据（可能非交易日）")
    if count:
        _enqueue_limit_list_after_daily(p, days=days)
    return count


def _sync_index_basic_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_index_basic_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.debug("未配置 TUSHARE_TOKEN，跳过指数列表同步")
        return 0
    force = bool((payload or {}).get("force"))
    count = sync_index_basic_to_db(force=force)
    if count:
        logger.info("指数列表已同步: %d 条", count)
    else:
        logger.info("指数列表已是最新，跳过同步")
    return count


def _sync_index_daily_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_index_daily_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.debug("未配置 TUSHARE_TOKEN，跳过指数日线同步")
        return 0
    p = payload or {}
    start_date = p.get("start_date")
    end_date = p.get("end_date")
    overwrite = bool(p.get("overwrite"))
    if start_date or end_date:
        count = sync_index_daily_to_db(
            start_date=start_date, end_date=end_date,
            code=p.get("code"), overwrite=overwrite,
        )
    else:
        if p.get("years"):
            days = int(p["years"]) * 366
        elif p.get("backfill"):
            days = int(p.get("days") or _env_int("XSHARE_INDEX_DAILY_BACKFILL_DAYS", 252))
        else:
            days = int(p.get("days") or _env_int("XSHARE_INDEX_DAILY_SYNC_DAYS", 1))
        count = sync_index_daily_to_db(days=days, code=p.get("code"), overwrite=overwrite)
        if count:
            logger.info("指数日线已同步: %d 条（最近 %d 个交易日）", count, days)
        else:
            logger.info("指数日线无可同步数据（可能非交易日或无 index_basic）")
        return count
    if count:
        logger.info("指数日线已同步: %d 条（区间 %s..%s overwrite=%s）",
                    count, start_date, end_date, overwrite)
    else:
        logger.info("指数日线无可同步数据（可能非交易日或无 index_basic）")
    return count


def _sync_etf_basic_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_etf_basic_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.debug("未配置 TUSHARE_TOKEN，跳过 ETF 列表同步")
        return 0
    force = bool((payload or {}).get("force"))
    count = sync_etf_basic_to_db(force=force)
    if count:
        logger.info("ETF 列表已同步: %d 条", count)
    else:
        logger.info("ETF 列表已是最新，跳过同步")
    return count


def _sync_fund_daily_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_fund_daily_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        logger.debug("未配置 TUSHARE_TOKEN，跳过 ETF 日线同步")
        return 0
    p = payload or {}
    start_date = p.get("start_date")
    end_date = p.get("end_date")
    overwrite = bool(p.get("overwrite"))
    if start_date or end_date:
        count = sync_fund_daily_to_db(
            start_date=start_date, end_date=end_date,
            code=p.get("code"), overwrite=overwrite,
        )
    else:
        if p.get("years"):
            days = int(p["years"]) * 366
        elif p.get("backfill"):
            days = int(p.get("days") or _env_int("XSHARE_FUND_DAILY_BACKFILL_DAYS", 252))
        else:
            days = int(p.get("days") or _env_int("XSHARE_FUND_DAILY_SYNC_DAYS", 1))
        count = sync_fund_daily_to_db(days=days, code=p.get("code"), overwrite=overwrite)
        if count:
            logger.info("ETF 日线已同步: %d 条（最近 %d 个交易日）", count, days)
        else:
            logger.info("ETF 日线无可同步数据（可能非交易日）")
        return count
    if count:
        logger.info("ETF 日线已同步: %d 条（区间 %s..%s overwrite=%s）",
                    count, start_date, end_date, overwrite)
    else:
        logger.info("ETF 日线无可同步数据（可能非交易日）")
    return count


def _sync_trade_cal_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_trade_cal_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    years = int((payload or {}).get("years") or _env_int("XSHARE_TRADE_CAL_YEARS", 3))
    count = sync_trade_cal_to_db(years=years)
    logger.info("交易日历已同步: %d 条", count)
    return count


def _sync_daily_basic_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_daily_basic_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    days = int(p.get("days") or (_env_int("XSHARE_DAILY_BACKFILL_DAYS", 5) if p.get("backfill") else 1))
    count = sync_daily_basic_to_db(days=days)
    logger.info("每日指标已同步: %d 条", count)
    return count


def _sync_finance_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_finance_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    limit = p.get("limit")
    force = bool(p.get("force"))
    count = sync_finance_to_db(limit=int(limit) if limit else None, force=force)
    logger.info("财务指标已同步: %d 只股票", count)
    return count


def _sync_quote_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.quote_snapshot import sync_quote_snapshot_to_db

    count = sync_quote_snapshot_to_db()
    logger.info("行情快照已同步: %d 条", count)
    return count


def _sync_moneyflow_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_moneyflow_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    start_date = p.get("start_date")
    end_date = p.get("end_date")
    overwrite = bool(p.get("overwrite"))
    if start_date or end_date:
        count = sync_moneyflow_to_db(
            start_date=start_date, end_date=end_date, overwrite=overwrite,
        )
        if count:
            logger.info(
                "个股资金流向已同步: %d 条（区间 %s..%s overwrite=%s）",
                count, start_date, end_date, overwrite,
            )
        else:
            logger.info("个股资金流向无可同步数据（可能非交易日）")
        return count
    if p.get("backfill"):
        days = int(p.get("days") or _env_int("XSHARE_DAILY_BACKFILL_DAYS", 252))
    else:
        days = int(p.get("days") or _env_int("XSHARE_MONEYFLOW_SYNC_DAYS", 1))
    count = sync_moneyflow_to_db(days=days, overwrite=overwrite)
    logger.info("个股资金流向已同步: %d 条", count)
    _try_enqueue_mainline_if_ready()
    return count


def _sync_sector_moneyflow_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_sector_moneyflow_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    days = int(p.get("days") or (_env_int("XSHARE_DAILY_BACKFILL_DAYS", 5) if p.get("backfill") else 1))
    count = sync_sector_moneyflow_to_db(days=days)
    logger.info("板块资金流向已同步: %d 条", count)
    _try_enqueue_mainline_if_ready()
    return count


def _sync_market_moneyflow_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_market_moneyflow_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    days = int(p.get("days") or (_env_int("XSHARE_DAILY_BACKFILL_DAYS", 5) if p.get("backfill") else 1))
    count = sync_market_moneyflow_to_db(days=days)
    _try_enqueue_mainline_if_ready()
    logger.info("大盘资金流向已同步: %d 条", count)
    return count


def _compute_limit_list_local(days: int = 1) -> int:
    """从 stock_daily + stock_basic 本地计算涨停列表，写入 limit_list。

    涨停判定：pct_chg ≥ 板块阈值（主板 10%/ST 5%、创业板/科创板 20%、北交所 30%）。
    连板数：gaps-and-islands 算法回溯连续涨停日。
    仅生成 limit_type='U' 行——mainline 只读 U，D/Z 不计算。

    ponytail: 回溯窗口固定 30 个自然日（约 22 交易日），足够算 5+ 连板；
    超长连板（如 10 连板）会因窗口不足而低估，但 mainline 按 5连+ 聚合，影响可忽略。
    """
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)

    # 确定目标交易日：stock_daily 中最近 days 个交易日
    target_dates = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily "
        "ORDER BY trade_date DESC LIMIT ?",
        [days],
    ).fetchall()
    if not target_dates:
        return 0
    target_set = {r[0] for r in target_dates}
    earliest = min(target_set)
    # 回溯窗口：目标日期前 30 自然日，覆盖连板计算
    lookback = (earliest - timedelta(days=30)).isoformat()

    # 一次性计算所有目标日期的涨停 + 连板数
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT sd.code, sd.trade_date, sd.close, sd.amount,
                   LAG(sd.close) OVER (PARTITION BY sd.code ORDER BY sd.trade_date) AS prev_close,
                   sb.name, sb.market
            FROM stock_daily sd
            JOIN stock_basic sb ON sb.code = sd.code
            WHERE sd.trade_date >= ?
        ),
        pct AS (
            SELECT code, trade_date, close, amount, name, market, prev_close,
                   CASE WHEN prev_close IS NOT NULL AND prev_close != 0
                        THEN ROUND((close - prev_close) / prev_close * 100, 2)
                        ELSE NULL END AS pct_chg
            FROM ranked
        ),
        is_limit AS (
            SELECT code, trade_date, close, amount, name, market, pct_chg,
                   CASE WHEN pct_chg IS NOT NULL AND (
                       (market = '主板' AND (
                           (name LIKE '%ST%' AND pct_chg >= 4.9) OR
                           (name NOT LIKE '%ST%' AND pct_chg >= 9.9)
                       )) OR
                       (market IN ('创业板', '科创板') AND pct_chg >= 19.9) OR
                       (market = '北交所' AND pct_chg >= 29.9)
                   ) THEN 1 ELSE 0 END AS is_up
            FROM pct
        ),
        streaks AS (
            SELECT code, trade_date, name, market, close, amount, pct_chg, is_up,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date) AS rn,
                   ROW_NUMBER() OVER (PARTITION BY code, is_up ORDER BY trade_date) AS rn2
            FROM is_limit
        ),
        runs AS (
            SELECT code, trade_date, name, market, close, amount, pct_chg, is_up, rn - rn2 AS grp
            FROM streaks
        ),
        consec AS (
            SELECT code, trade_date, name, market, close, amount, pct_chg, is_up,
                   CASE WHEN is_up = 1
                        THEN ROW_NUMBER() OVER (PARTITION BY code, grp ORDER BY trade_date)
                        ELSE 0 END AS limit_times
            FROM runs
        )
        SELECT code, trade_date, name, market, close, pct_chg, amount, limit_times
        FROM consec
        WHERE is_up = 1 AND trade_date IN ?
        """,
        [lookback, list(target_set)],
    ).fetchall()

    from xshare.data import watermark as wm
    if not rows:
        # 有日线但当日无涨停：算完成功，避免被当成空数据重试。
        for d in target_set:
            wm.set_watermark(wm.DATASET_LIMIT_LIST, d, wm.STATUS_OK, 0)
        return len(target_set)

    # 写入 limit_list（仅 U 类型，先删旧 U 行再插入）
    fetched = 0
    for d in sorted(target_set, reverse=True):
        iso = d.isoformat() if hasattr(d, 'isoformat') else str(d)
        day_rows = [r for r in rows if r[1] == d]
        if not day_rows:
            wm.set_watermark(wm.DATASET_LIMIT_LIST, d, wm.STATUS_OK, 0)
            continue
        conn.execute(
            "DELETE FROM limit_list WHERE trade_date = ? AND limit_type = 'U'",
            [d],
        )
        conn.executemany(
            """
            INSERT INTO limit_list
                (trade_date, code, name, industry, close, pct_chg, amount,
                 limit_amount, float_mv, turnover_ratio,
                 first_time, last_time, open_times, up_stat, limit_times, limit_type)
            VALUES (?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, 'U')
            """,
            [(d, r[0], r[2], r[4], r[5], r[6], r[7]) for r in day_rows],
        )
        fetched += len(day_rows)
        wm.set_watermark(wm.DATASET_LIMIT_LIST, d, wm.STATUS_OK, len(day_rows))
    return fetched

def _stock_daily_has_session_date(session: date) -> bool:
    from xshare.data.db import get_conn

    row = get_conn().execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()
    latest = row[0] if row else None
    if latest is None:
        return False
    latest_d = latest if hasattr(latest, "year") else date.fromisoformat(str(latest)[:10])
    return latest_d >= session


def _limit_list_after_daily_ready() -> bool:
    """16:00 窗口内须等 stock_daily 含当日，避免用昨日行情算涨停后标 ok。"""
    now = datetime.now()
    if not _daily_sync_window_open(now):
        return True
    return _stock_daily_has_session_date(now.date())


def _enqueue_limit_list_after_daily(payload: dict, days: int) -> None:
    """daily 入库后再入队 limit_list，保证按当日 stock_daily 计算。"""
    try:
        from xshare.data.task_queue import enqueue

        ll: dict = {"days": max(1, int(days))}
        if payload.get("backfill") or payload.get("start_date") or payload.get("years"):
            ll["backfill"] = True
        enqueue(LIMIT_LIST_JOB, payload=ll, trigger="schedule", priority=4)
    except Exception as exc:
        logger.warning("daily 后入队 limit_list 失败: %s", exc)
    _try_enqueue_mainline_if_ready()


def _sync_limit_list_blocking(payload: dict | None = None) -> int:
    """从 stock_daily + stock_basic 本地计算涨跌停列表，写入 limit_list。

    不依赖 Tushare limit_list_d 接口（该接口通常比 daily 延迟 1-2 小时）。
    仅生成 limit_type='U'（涨停）行——mainline 只读 U 类型。
    """
    if not _limit_list_after_daily_ready():
        logger.info("涨跌停列表等待 stock_daily 当日数据")
        return 0
    p = payload or {}
    days = int(p.get("days") or (_env_int("XSHARE_DAILY_BACKFILL_DAYS", 5) if p.get("backfill") else 1))
    count = _compute_limit_list_local(days=days)
    _try_enqueue_mainline_if_ready()
    logger.info("涨跌停列表已本地计算: %d 条", count)
    return count




def _sync_concept_board_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_concept_board_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    days = int(p.get("days") or (_env_int("XSHARE_DAILY_BACKFILL_DAYS", 5) if p.get("backfill") else 1))
    count = sync_concept_board_to_db(days=days)
    logger.info("概念题材板块已同步: %d 条", count)
    _try_enqueue_mainline_if_ready()
    return count


def _sync_concept_member_blocking(payload: dict | None = None) -> int:
    from xshare.data.sources.tushare_source import sync_concept_member_to_db

    if not os.environ.get("TUSHARE_TOKEN"):
        return 0
    p = payload or {}
    days = int(p.get("days") or (_env_int("XSHARE_DAILY_BACKFILL_DAYS", 5) if p.get("backfill") else 1))
    top_n = p.get("top_n")
    top_n = int(top_n) if top_n is not None else 24
    count = sync_concept_member_to_db(days=days, top_n=top_n)
    logger.info("概念题材成分已同步: %d 条", count)
    _try_enqueue_mainline_if_ready()
    return count


# mainline 依赖表：这些表同步到同一交易日后，mainline 才有意义计算。
# stock_daily 作为基准日期参照（最先入库）。
# limit_list 由 stock_daily 本地计算，读路径可即时补算，不当调度硬依赖。
_MAINLINE_DEP_TABLES = (
    ("concept_board", None),
    ("sector_moneyflow", "概念"),
    ("stock_moneyflow", None),
    ("concept_member", None),
)


def _mainline_deps_ready() -> tuple[bool, str | None]:
    """检查 mainline 4 张依赖表是否都已同步到 stock_daily 的最新交易日。

    Returns:
        (ready, target_date) — ready=True 时 target_date 为 YYYY-MM-DD；
        ready=False 时 target_date 为缺失的表名（调试用）或 None。
    """
    from xshare.data.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()
    target = row[0] if row else None
    if target is None:
        return False, None
    for table, where in _MAINLINE_DEP_TABLES:
        sql = f"SELECT MAX(trade_date) FROM {table}"
        params = []
        if where:
            sql += " WHERE content_type = ?"
            params.append(where)
        r = conn.execute(sql, params).fetchone()
        latest = r[0] if r else None
        if latest is None or str(latest) < str(target):
            return False, table
    return True, str(target)


def _try_enqueue_mainline_if_ready() -> None:
    """依赖任务完成后：检查就绪则入队 mainline（dedup 防重复）。

    供 _sync_limit_list / _sync_moneyflow 等 handler 成功后调用。
    """
    cfg = get_one(MAINLINE_JOB)
    if not cfg or not cfg.get("enabled"):
        return
    ready, info = _mainline_deps_ready()
    if not ready:
        logger.debug("mainline 依赖未就绪（缺 %s），跳过入队", info)
        return
    try:
        from xshare.data.task_queue import enqueue
        task_id = enqueue(MAINLINE_JOB, trigger="schedule", priority=3)
        logger.info("mainline 依赖就绪(target=%s)，重算入队 #%d", info, task_id)
    except Exception as exc:
        logger.debug("mainline 入队失败: %s", exc)

def _sync_mainline_blocking(payload: dict | None = None) -> int:
    """计算三维度共振主线结果并写入 mainline_cache。
    基准日期取自 _score_mainline_from_db 返回的 data_date（analysis_date），
    确保跨表日期对齐后的缓存键一致。
    """
    from xshare.data.db import get_conn
    from xshare.tools.market_mainline import _score_mainline_from_db

    p = payload or {}
    sector_top_n = int(p.get("sector_top_n") or 8)
    strong_limit = int(p.get("strong_limit") or 10)

    result = _score_mainline_from_db(sector_top_n, strong_limit)
    if result is None:
        raise RuntimeError("_score_mainline_from_db 返回 None，依赖表数据不足")

    analysis_date = result.get("data_date")
    if not analysis_date:
        raise RuntimeError("_score_mainline_from_db 未返回 data_date")

    from datetime import datetime as _dt
    result_json = json.dumps(result, ensure_ascii=False, default=str)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO mainline_cache (trade_date, result_json, cached_at)
        VALUES (?, ?, ?)
        ON CONFLICT (trade_date) DO UPDATE SET result_json = excluded.result_json, cached_at = excluded.cached_at
        """,
        [analysis_date, result_json, _dt.now()],
    )
    logger.info("主线缓存已更新: trade_date=%s", analysis_date)
    return 1


_BLOCKING_HANDLERS = {
    NEWS_JOB: _sync_news_blocking,
    STOCK_JOB: _sync_stock_blocking,
    DAILY_JOB: _sync_daily_blocking,
    INDEX_BASIC_JOB: _sync_index_basic_blocking,
    INDEX_DAILY_JOB: _sync_index_daily_blocking,
    ETF_BASIC_JOB: _sync_etf_basic_blocking,
    FUND_DAILY_JOB: _sync_fund_daily_blocking,
    TRADE_CAL_JOB: _sync_trade_cal_blocking,
    DAILY_BASIC_JOB: _sync_daily_basic_blocking,
    FINANCE_JOB: _sync_finance_blocking,
    QUOTE_JOB: _sync_quote_blocking,
    MONEYFLOW_JOB: _sync_moneyflow_blocking,
    SECTOR_MONEYFLOW_JOB: _sync_sector_moneyflow_blocking,
    MARKET_MONEYFLOW_JOB: _sync_market_moneyflow_blocking,
    LIMIT_LIST_JOB: _sync_limit_list_blocking,
    CONCEPT_BOARD_JOB: _sync_concept_board_blocking,
    CONCEPT_MEMBER_JOB: _sync_concept_member_blocking,
    MAINLINE_JOB: _sync_mainline_blocking,
}


# ─── 后台循环 ────────────────────────────────────────────────────────────────

async def sync_loop(job: str) -> None:
    """后台循环：interval 任务按间隔入队；日历任务在交易日 16:00 入队一次。"""
    while True:
        cfg = get_one(job)
        if not cfg or not cfg["enabled"]:
            await asyncio.sleep(_POLL_SECONDS)
            continue

        if job in CALENDAR_JOBS or job == MAINLINE_JOB:
            await _calendar_loop_iteration(job, cfg)
        else:
            await _interval_loop_iteration(job, cfg)


async def _interval_loop_iteration(job: str, cfg: dict) -> None:
    interval = cfg["interval_minutes"] * 60
    deadline = asyncio.get_event_loop().time() + interval
    while True:
        now = asyncio.get_event_loop().time()
        if now >= deadline:
            break
        await asyncio.sleep(min(_POLL_SECONDS, deadline - now))
        cfg = get_one(job)
        if not cfg or not cfg["enabled"] or cfg["interval_minutes"] * 60 != interval:
            return

    cfg = get_one(job)
    if cfg and cfg["enabled"]:
        if job in TRADING_HOURS_JOBS and not _in_trading_hours():
            logger.debug("非交易时段，跳过 %s 入队", job)
            return
        try:
            from xshare.data.task_queue import enqueue
            task_id = enqueue(job, trigger="schedule")
            logger.info("定时任务 %s 已入队: #%d", job, task_id)
        except Exception as exc:
            logger.warning("定时任务 %s 入队失败: %s", job, exc)


async def _calendar_loop_iteration(job: str, cfg: dict) -> None:
    """等到下一窗口；若今日尚未成功则入队；daily 额外扫 watermark 补洞。"""
    now = datetime.now()
    eligible, _ = check_calendar_window(job, now=now)
    if not eligible:
        # 睡到窗口或最多 _POLL_SECONDS
        nxt = _next_daily_window_open(now)
        wait = max(1.0, min(_POLL_SECONDS, (nxt - now).total_seconds()))
        await asyncio.sleep(wait)
        return

    last = _parse_local_ts(cfg.get("last_run_at"))
    already_ok_today = (
        last is not None
        and last.date() == now.date()
        and cfg.get("last_status") == "ok"
    )

    from xshare.data.task_queue import enqueue

    # mainline 不走 16:00 盲目入队，由依赖就绪检查单独处理（见下方）
    if not already_ok_today and job != MAINLINE_JOB:
        try:
            task_id = enqueue(job, trigger="schedule")
            logger.info("日历任务 %s 已入队: #%d", job, task_id)
        except Exception as exc:
            logger.warning("日历任务 %s 入队失败: %s", job, exc)

    # mainline：仅在依赖表全部就绪后入队（不再 16:00 盲目并行触发）。
    # 依赖表完成时会通过 _try_enqueue_mainline_if_ready 主动触发；
    # 这里作为兜底：每 30s 轮询一次就绪状态。
    if job == MAINLINE_JOB:
        from xshare.data.db import get_conn
        ready, target = _mainline_deps_ready()
        if ready:
            try:
                cached_date = get_conn().execute(
                    "SELECT MAX(trade_date) FROM mainline_cache"
                ).fetchone()[0]
            except Exception:
                cached_date = None
            if cached_date is None or str(cached_date) < str(target):
                try:
                    task_id = enqueue(job, trigger="schedule", priority=3)
                    logger.info(
                        "mainline 依赖就绪(target=%s, cached=%s)，入队 #%d",
                        target, cached_date, task_id,
                    )
                except Exception as exc:
                    logger.warning("mainline 入队失败: %s", exc)
            else:
                logger.debug("mainline 缓存已最新(cached=%s=target)，跳过", target)
        else:
            logger.debug("mainline 依赖未就绪（缺 %s），等待", target)

    # daily / index_daily：补洞（watermark 缺口）
    if job == DAILY_JOB:
        try:
            from xshare.data.sources.tushare_source import find_missing_daily_dates
            gaps = find_missing_daily_dates(_env_int("XSHARE_DAILY_GAP_LOOKBACK", 30))
            if gaps:
                days = max(len(gaps), _env_int("XSHARE_DAILY_SYNC_DAYS", 1))
                task_id = enqueue(
                    DAILY_JOB,
                    payload={"days": days, "backfill": True},
                    trigger="schedule",
                    priority=2,
                )
                logger.info("日线补洞 %d 天，已入队 backfill #%d", len(gaps), task_id)
        except Exception as exc:
            logger.debug("日线补洞扫描失败: %s", exc)

    if job == INDEX_DAILY_JOB:
        try:
            from xshare.data.sources.tushare_source import find_missing_index_daily_dates
            gaps = find_missing_index_daily_dates(_env_int("XSHARE_INDEX_DAILY_GAP_LOOKBACK", 30))
            if gaps:
                days = max(len(gaps), _env_int("XSHARE_INDEX_DAILY_SYNC_DAYS", 1))
                task_id = enqueue(
                    INDEX_DAILY_JOB,
                    payload={"days": days, "backfill": True},
                    trigger="schedule",
                    priority=2,
                )
                logger.info("指数日线补洞 %d 天，已入队 backfill #%d", len(gaps), task_id)
        except Exception as exc:
            logger.debug("指数日线补洞扫描失败: %s", exc)

    if job == FUND_DAILY_JOB:
        try:
            from xshare.data.sources.tushare_source import find_missing_fund_daily_dates
            gaps = find_missing_fund_daily_dates(_env_int("XSHARE_FUND_DAILY_GAP_LOOKBACK", 30))
            if gaps:
                days = max(len(gaps), _env_int("XSHARE_FUND_DAILY_SYNC_DAYS", 1))
                task_id = enqueue(
                    FUND_DAILY_JOB,
                    payload={"days": days, "backfill": True},
                    trigger="schedule",
                    priority=2,
                )
                logger.info("ETF 日线补洞 %d 天，已入队 backfill #%d", len(gaps), task_id)
        except Exception as exc:
            logger.debug("ETF 日线补洞扫描失败: %s", exc)


    if job == MONEYFLOW_JOB:
        try:
            from xshare.data.sources.tushare_source import find_missing_moneyflow_dates
            gaps = find_missing_moneyflow_dates(_env_int("XSHARE_MONEYFLOW_GAP_LOOKBACK", 30))
            if gaps:
                days = max(len(gaps), _env_int("XSHARE_MONEYFLOW_SYNC_DAYS", 1))
                task_id = enqueue(
                    MONEYFLOW_JOB,
                    payload={"days": days, "backfill": True},
                    trigger="schedule",
                    priority=2,
                )
                logger.info("个股资金流向补洞 %d 天，已入队 backfill #%d", len(gaps), task_id)
        except Exception as exc:
            logger.debug("个股资金流向补洞扫描失败: %s", exc)

    # 窗口内已处理：睡到次日，避免重复入队
    await asyncio.sleep(_POLL_SECONDS)
