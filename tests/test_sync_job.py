import json

import pytest

from xshare.tools import sync_job
from xshare.data import sync_config


@pytest.fixture
def sync_db(db_conn):
    """db_conn + 初始化 sync_config 种子数据。"""
    sync_config.init_sync_config()
    return db_conn


@pytest.mark.asyncio
async def test_sync_job_status(sync_db):
    resp = await sync_job.sync_job({"action": "status"})
    data = json.loads(resp)

    jobs = {j["job"] for j in data["jobs"]}
    assert "news" in jobs
    assert "stock_basic" in jobs
    assert "daily" in jobs
    for j in data["jobs"]:
        assert "enabled" in j
        assert "interval_minutes" in j


@pytest.mark.asyncio
async def test_sync_job_config_update_interval(sync_db):
    resp = await sync_job.sync_job({
        "action": "config",
        "job": "news",
        "interval_minutes": 30,
    })
    data = json.loads(resp)

    assert "已更新" in data["message"]
    assert data["job"]["interval_minutes"] == 30


@pytest.mark.asyncio
async def test_sync_job_config_disable(sync_db):
    resp = await sync_job.sync_job({
        "action": "config",
        "job": "news",
        "enabled": False,
    })
    data = json.loads(resp)

    assert data["job"]["enabled"] is False


@pytest.mark.asyncio
async def test_sync_job_config_invalid_interval(sync_db):
    resp = await sync_job.sync_job({
        "action": "config",
        "job": "news",
        "interval_minutes": 0,
    })
    data = json.loads(resp)

    assert "error" in data
    assert "正整数" in data["error"]


@pytest.mark.asyncio
async def test_sync_job_config_unknown_job(sync_db):
    resp = await sync_job.sync_job({
        "action": "config",
        "job": "unknown",
        "interval_minutes": 10,
    })
    data = json.loads(resp)

    assert "error" in data
    assert "未知任务" in data["error"]


@pytest.mark.asyncio
async def test_sync_job_config_missing_params(sync_db):
    resp = await sync_job.sync_job({"action": "config", "job": "news"})
    data = json.loads(resp)

    assert "error" in data
    assert "enabled 或 interval_minutes" in data["error"]


@pytest.mark.asyncio
async def test_sync_job_run_aliases_enqueue(sync_db):
    resp = await sync_job.sync_job({"action": "run", "job": "news"})
    data = json.loads(resp)

    assert "queued" in data
    assert data["queued"][0]["job"] == "news"


@pytest.mark.asyncio
async def test_sync_job_unknown_action(sync_db):
    resp = await sync_job.sync_job({"action": "restart"})
    data = json.loads(resp)

    assert "error" in data
    assert "未知 action" in data["error"]
    assert "run" in data["valid_actions"]


@pytest.mark.asyncio
async def test_sync_job_coverage(sync_db):
    resp = await sync_job.sync_job({"action": "coverage", "lookback_trading_days": 5})
    data = json.loads(resp)
    assert "stock" in data and "index" in data and "fund" in data
    assert data["stock"]["target_days"] == 5
    assert "sufficient" in data["stock"]


@pytest.mark.asyncio
async def test_sync_job_cancel(sync_db):
    from xshare.data.task_queue import enqueue

    task_id = enqueue("news")
    resp = await sync_job.sync_job({"action": "cancel", "task_id": task_id})
    data = json.loads(resp)
    assert "已取消" in data["message"]


@pytest.mark.asyncio
async def test_sync_job_cleanup(sync_db):
    from datetime import datetime, timedelta, timezone

    from xshare.data.sqlite_db import get_sqlite_conn, ts
    from xshare.data.task_queue import enqueue, get_task, _claim_next, _complete

    task_id = enqueue("news")
    _claim_next()
    _complete(task_id, {"synced": 0})
    old_ts = ts(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=60))
    get_sqlite_conn().execute(
        "UPDATE sync_task_queue SET finished_at=? WHERE id=?",
        [old_ts, task_id],
    )
    resp = await sync_job.sync_job({"action": "cleanup", "retain_days": 30, "retain_count": 0})
    data = json.loads(resp)
    assert data["deleted"] >= 1
    assert get_task(task_id) is None


@pytest.mark.asyncio
async def test_sync_job_enqueue_backfill(sync_db):
    resp = await sync_job.sync_job({
        "action": "enqueue",
        "job": "daily",
        "days": 252,
        "backfill": True,
    })
    data = json.loads(resp)
    from xshare.data.task_queue import get_task
    task = get_task(data["queued"][0]["task_id"])
    assert task["payload"]["backfill"] is True
    assert task["payload"]["days"] == 252
