import asyncio
from datetime import datetime

import pytest

from xshare.data import sync_config


def test_daily_window_open_when_trade_day_after_1600(monkeypatch):
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: True)
    now = datetime(2026, 7, 20, 16, 0, 0)
    assert sync_config._daily_sync_window_open(now) is True


def test_daily_window_closed_before_1600_even_trade_day(monkeypatch):
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: True)
    now = datetime(2026, 7, 20, 15, 59, 59)
    assert sync_config._daily_sync_window_open(now) is False


def test_daily_window_closed_on_non_trade_day_even_after_1600(monkeypatch):
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: False)
    now = datetime(2026, 7, 20, 16, 0, 0)
    assert sync_config._daily_sync_window_open(now) is False


def test_estimate_next_run_at_disabled():
    cfg = {"enabled": False, "interval_minutes": 15, "last_run_at": "2026-07-20 10:00:00"}
    assert sync_config.estimate_next_run_at("news", cfg) is None


def test_estimate_next_run_at_from_last_run(monkeypatch):
    monkeypatch.setattr(sync_config, "_daily_sync_window_open", lambda now=None: True)
    now = datetime(2026, 7, 20, 11, 15, 0)
    cfg = {"enabled": True, "interval_minutes": 30, "last_run_at": "2026-07-20 11:00:00"}
    assert sync_config.estimate_next_run_at("news", cfg, now) == "2026-07-20 11:30:00"


def test_estimate_next_run_at_daily_outside_window(monkeypatch):
    monkeypatch.setattr(sync_config, "_daily_sync_window_open", lambda now=None: False)
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: d.weekday() < 5)
    now = datetime(2026, 7, 20, 10, 0, 0)  # Monday
    cfg = {"enabled": True, "interval_minutes": 240}
    assert sync_config.estimate_next_run_at("daily", cfg, now) == "2026-07-20 16:00:00"


def test_get_all_includes_next_run_at_and_new_jobs(db_conn):
    sync_config.init_sync_config()
    jobs = sync_config.get_all()
    names = {j["job"] for j in jobs}
    assert "trade_cal" in names
    assert "daily_basic" in names
    assert "finance" in names
    assert "fund_nav" not in names
    assert "index_basic" in names
    assert "index_daily" in names
    assert "etf_basic" in names
    assert "fund_daily" in names
    for j in jobs:
        assert "next_run_at" in j
        assert "schedule" in j
        if j["enabled"]:
            assert j["next_run_at"] is not None
        if j["job"] in sync_config.CALENDAR_JOBS:
            assert j["schedule"] == "calendar_1600"


def test_mainline_deps_ready_all_tables_aligned(db_conn):
    """所有依赖表都达到 stock_daily 最新交易日 → ready=True。"""
    db_conn.execute("INSERT INTO stock_daily (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_board (trade_date, code) VALUES ('2026-08-17', 'BK0001')")
    db_conn.execute("INSERT INTO sector_moneyflow (trade_date, content_type, code) VALUES ('2026-08-17', '概念', 'BK0001')")
    db_conn.execute("INSERT INTO stock_moneyflow (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_member (trade_date, code, concept_code) VALUES ('2026-08-17', '000001.SZ', 'BK0001')")
    ready, target = sync_config._mainline_deps_ready()
    assert ready is True
    assert target == "2026-08-17"


def test_mainline_deps_ready_missing_table(db_conn):
    """某依赖表落后 → ready=False, info=表名。"""
    db_conn.execute("INSERT INTO stock_daily (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_board (trade_date, code) VALUES ('2026-08-17', 'BK0001')")
    db_conn.execute("INSERT INTO sector_moneyflow (trade_date, content_type, code) VALUES ('2026-08-17', '概念', 'BK0001')")
    db_conn.execute("INSERT INTO stock_moneyflow (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    # concept_member 缺失
    ready, info = sync_config._mainline_deps_ready()
    assert ready is False
    assert info == "concept_member"


def test_mainline_deps_ready_stale_table(db_conn):
    """某依赖表日期落后 → ready=False。"""
    db_conn.execute("INSERT INTO stock_daily (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_board (trade_date, code) VALUES ('2026-08-17', 'BK0001')")
    db_conn.execute("INSERT INTO sector_moneyflow (trade_date, content_type, code) VALUES ('2026-08-17', '概念', 'BK0001')")
    db_conn.execute("INSERT INTO stock_moneyflow (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_member (trade_date, code, concept_code) VALUES ('2026-08-12', '000001.SZ', 'BK0001')")
    ready, info = sync_config._mainline_deps_ready()
    assert ready is False
    assert info == "concept_member"


def test_try_enqueue_mainline_skips_when_deps_not_ready(db_conn):
    """依赖未就绪时 _try_enqueue_mainline_if_ready 不入队。"""
    db_conn.execute("INSERT INTO stock_daily (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    # concept_board 缺失：依赖未就绪
    called = []
    sync_config._try_enqueue_mainline_if_ready()
    assert called == []


def test_try_enqueue_mainline_enqueues_when_deps_ready(db_conn, monkeypatch):
    """依赖全部就绪时 _try_enqueue_mainline_if_ready 入队 mainline（priority=3）。"""
    db_conn.execute("INSERT INTO stock_daily (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_board (trade_date, code) VALUES ('2026-08-17', 'BK0001')")
    db_conn.execute("INSERT INTO sector_moneyflow (trade_date, content_type, code) VALUES ('2026-08-17', '概念', 'BK0001')")
    db_conn.execute("INSERT INTO stock_moneyflow (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_member (trade_date, code, concept_code) VALUES ('2026-08-17', '000001.SZ', 'BK0001')")

    # mainline 需在 sync_config 表中存在且 enabled
    sync_config.init_sync_config()
    from xshare.data import task_queue
    enqueued = []
    monkeypatch.setattr(task_queue, "enqueue", lambda *a, **k: enqueued.append((a, k)) or 42)

    sync_config._try_enqueue_mainline_if_ready()
    assert len(enqueued) == 1
    args, kwargs = enqueued[0]
    assert args[0] == sync_config.MAINLINE_JOB
    assert kwargs.get("priority") == 3


def test_calendar_window_bypassed_by_start_date(monkeypatch):
    monkeypatch.setattr(sync_config, "_daily_sync_window_open", lambda now=None: False)
    now = datetime(2026, 7, 20, 10, 0, 0)
    eligible, reason = sync_config.check_calendar_window(
        "daily", payload={"start_date": "2026-01-01", "end_date": "2026-07-01"}, now=now
    )
    assert eligible is True
    assert reason == ""


def test_limit_list_ready_outside_window(monkeypatch):
    monkeypatch.setattr(sync_config, "_daily_sync_window_open", lambda now=None: False)
    assert sync_config._limit_list_after_daily_ready() is True


def test_limit_list_waits_for_same_day_daily(monkeypatch):
    monkeypatch.setattr(sync_config, "_daily_sync_window_open", lambda now=None: True)
    monkeypatch.setattr(sync_config, "_stock_daily_has_session_date", lambda session: False)
    assert sync_config._limit_list_after_daily_ready() is False


def test_mainline_deps_ready_without_limit_list(db_conn):
    """limit_list 不是调度硬依赖。"""
    db_conn.execute("INSERT INTO stock_daily (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_board (trade_date, code) VALUES ('2026-08-17', 'BK0001')")
    db_conn.execute("INSERT INTO sector_moneyflow (trade_date, content_type, code) VALUES ('2026-08-17', '概念', 'BK0001')")
    db_conn.execute("INSERT INTO stock_moneyflow (code, trade_date) VALUES ('000001.SZ', '2026-08-17')")
    db_conn.execute("INSERT INTO concept_member (trade_date, code, concept_code) VALUES ('2026-08-17', '000001.SZ', 'BK0001')")
    ready, target = sync_config._mainline_deps_ready()
    assert ready is True
    assert target == "2026-08-17"


@pytest.mark.asyncio
async def test_sync_loop_routes_mainline_to_calendar(monkeypatch, db_conn):
    sync_config.init_sync_config()
    called = []

    async def fake(job, cfg):
        called.append(job)
        raise asyncio.CancelledError

    monkeypatch.setattr(sync_config, "_calendar_loop_iteration", fake)
    with pytest.raises(asyncio.CancelledError):
        await sync_config.sync_loop(sync_config.MAINLINE_JOB)
    assert called == [sync_config.MAINLINE_JOB]


def test_mainline_exempt_from_calendar_window():
    """mainline 不受 16:00 窗口限制：任何时刻都 eligible。"""
    # 非交易日、窗口外、backfill=False
    now = datetime(2026, 7, 19, 9, 0, 0)  # 周日早 9 点
    eligible, reason = sync_config.check_calendar_window(sync_config.MAINLINE_JOB, now=now)
    assert eligible is True
    assert reason == ""


def test_estimate_next_run_at_mainline_not_yet_run():
    """mainline 未跑过 → 30s 后重试。"""
    now = datetime(2026, 7, 20, 9, 0, 0)
    cfg = {"enabled": True, "interval_minutes": 1440, "last_run_at": None, "last_status": None}
    result = sync_config.estimate_next_run_at(sync_config.MAINLINE_JOB, cfg, now)
    assert result == "2026-07-20 09:00:30"


def test_estimate_next_run_at_mainline_done_today():
    """mainline 今日已成功 → None（不再预估）。"""
    now = datetime(2026, 7, 20, 16, 30, 0)
    cfg = {"enabled": True, "interval_minutes": 1440,
           "last_run_at": "2026-07-20 16:10:00", "last_status": "ok"}
    assert sync_config.estimate_next_run_at(sync_config.MAINLINE_JOB, cfg, now) is None
