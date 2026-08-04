from datetime import datetime

from xshare.data import sync_config


def test_daily_window_open_when_trade_day_after_1700(monkeypatch):
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: True)
    now = datetime(2026, 7, 20, 17, 0, 0)
    assert sync_config._daily_sync_window_open(now) is True


def test_daily_window_closed_before_1700_even_trade_day(monkeypatch):
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: True)
    now = datetime(2026, 7, 20, 16, 59, 59)
    assert sync_config._daily_sync_window_open(now) is False


def test_daily_window_closed_on_non_trade_day_even_after_1700(monkeypatch):
    monkeypatch.setattr(sync_config, "_is_trade_day", lambda d: False)
    now = datetime(2026, 7, 20, 17, 0, 0)
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
    assert sync_config.estimate_next_run_at("daily", cfg, now) == "2026-07-20 17:00:00"


def test_get_all_includes_next_run_at_and_new_jobs(db_conn):
    sync_config.init_sync_config()
    jobs = sync_config.get_all()
    names = {j["job"] for j in jobs}
    assert "trade_cal" in names
    assert "daily_basic" in names
    assert "finance" in names
    assert "fund_nav" in names
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
            assert j["schedule"] == "calendar_1700"
