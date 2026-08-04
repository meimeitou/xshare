"""任务队列状态机回归测试。"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from xshare.data import task_queue
from xshare.data.sqlite_db import get_sqlite_conn, ts


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def queue_db(db_conn):
    """sync_task_queue 现在在 SQLite；返回 SQLite 连接供原始 SQL 探针使用。
    db_conn 已建好两库的内存表。"""
    return get_sqlite_conn()


# ─── enqueue ─────────────────────────────────────────────────────────────────


def test_enqueue_creates_queued_task(queue_db):
    task_id = task_queue.enqueue("daily", payload={"days": 5})
    task = task_queue.get_task(task_id)

    assert task is not None
    assert task["task_type"] == "daily"
    assert task["status"] == "queued"
    assert task["payload"] == {"days": 5}
    assert task["trigger"] == "manual"
    assert task["attempts"] == 0
    assert task["queued_at"] is not None


def test_enqueue_rejects_unknown_type(queue_db):
    with pytest.raises(ValueError, match="未知任务类型"):
        task_queue.enqueue("bad_job")


def test_enqueue_default_trigger_is_manual(queue_db):
    task_id = task_queue.enqueue("news")
    assert task_queue.get_task(task_id)["trigger"] == "manual"


def test_enqueue_schedule_trigger(queue_db):
    task_id = task_queue.enqueue("daily", trigger="schedule")
    assert task_queue.get_task(task_id)["trigger"] == "schedule"


# ─── _claim_next ─────────────────────────────────────────────────────────────


def test_claim_next_marks_running(queue_db):
    task_id = task_queue.enqueue("news")
    claimed = task_queue._claim_next()

    assert claimed is not None
    assert claimed["id"] == task_id
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["started_at"] is not None


def test_claim_next_empty_queue_returns_none(queue_db):
    assert task_queue._claim_next() is None


def test_claim_next_respects_priority(queue_db):
    low_id  = task_queue.enqueue("daily", priority=9)
    high_id = task_queue.enqueue("news",  priority=1)

    claimed = task_queue._claim_next()

    assert claimed["id"] == high_id   # 低数字 = 高优先级


def test_claim_next_fifo_within_same_priority(queue_db):
    first_id  = task_queue.enqueue("daily", priority=5)
    second_id = task_queue.enqueue("news",  priority=5)

    first_claimed = task_queue._claim_next()
    assert first_claimed["id"] == first_id


def test_claim_next_respects_next_run_at(queue_db):
    """被延迟（next_run_at 在未来）的任务不应被认领。"""
    task_id = task_queue.enqueue("daily")
    queue_db.execute(
        "UPDATE sync_task_queue SET next_run_at=? WHERE id=?",
        [ts(_utc_now() + timedelta(hours=1)), task_id],
    )

    assert task_queue._claim_next() is None


def test_claim_next_keeps_fresh_lease(queue_db):
    """刚认领的任务 lease 未过期时，不应被当成僵尸回收。"""
    task_id = task_queue.enqueue("daily")
    claimed = task_queue._claim_next()
    assert claimed["id"] == task_id
    assert claimed["attempts"] == 1

    # 队列里没有其他 queued 任务；若误把 running 当僵尸回收，会再次认领同一任务
    assert task_queue._claim_next() is None
    assert task_queue.get_task(task_id)["attempts"] == 1


def test_claim_next_reclaims_zombie(queue_db):
    """lease_at 已超时的 running 任务应被回收为 queued，并可被重新认领。"""
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()

    # 伪造过期心跳（UTC，与 SQL current_timestamp 同坐标系）
    old_lease = ts(_utc_now() - timedelta(seconds=task_queue._LEASE_TTL + 10))
    queue_db.execute(
        "UPDATE sync_task_queue SET lease_at=? WHERE id=?",
        [old_lease, task_id],
    )

    reclaimed = task_queue._claim_next()
    assert reclaimed is not None
    assert reclaimed["id"] == task_id
    assert reclaimed["attempts"] == 2  # 第二次认领


# ─── _complete ───────────────────────────────────────────────────────────────


def test_complete_marks_success(queue_db):
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()
    task_queue._complete(task_id, {"synced": 42})

    task = task_queue.get_task(task_id)
    assert task["status"] == "success"
    assert task["result"]["synced"] == 42
    assert task["finished_at"] is not None


def test_apply_job_result_ok_marks_success(queue_db):
    task_id = task_queue.enqueue("news")
    task_queue._claim_next()
    task_queue._apply_job_result(task_id, {"job": "news", "status": "ok", "synced": 3})
    assert task_queue.get_task(task_id)["status"] == "success"


def test_apply_job_result_skipped_marks_skipped(queue_db):
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()
    task_queue._apply_job_result(
        task_id,
        {"job": "daily", "status": "skipped", "reason": "窗口外"},
    )
    task = task_queue.get_task(task_id)
    assert task["status"] == "skipped"
    assert task["last_error"] == "窗口外"
    assert task["result"]["reason"] == "窗口外"


def test_apply_job_result_error_requeues(queue_db):
    """run_job 返回 error 时应走 _fail 退避重试，而不是记成 success。"""
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()
    task_queue._apply_job_result(
        task_id,
        {"job": "daily", "status": "error", "error": "timeout"},
    )
    task = task_queue.get_task(task_id)
    assert task["status"] == "queued"
    assert task["last_error"] == "timeout"
    assert task["next_run_at"] is not None


def test_apply_job_result_error_exhausts_to_error(queue_db):
    task_id = task_queue.enqueue("daily")
    queue_db.execute(
        "UPDATE sync_task_queue SET max_attempts=1 WHERE id=?", [task_id]
    )
    task_queue._claim_next()
    task_queue._apply_job_result(
        task_id,
        {"job": "daily", "status": "error", "error": "persistent"},
    )
    assert task_queue.get_task(task_id)["status"] == "error"


# ─── _fail ───────────────────────────────────────────────────────────────────


def test_fail_under_max_attempts_requeues(queue_db):
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()
    task_queue._fail(task_id, "connection error")

    task = task_queue.get_task(task_id)
    assert task["status"] == "queued"
    assert task["last_error"] == "connection error"
    assert task["next_run_at"] is not None  # 退避延迟

    # next_run_at 必须与 SQL UTC 同坐标系：延迟约 60s，绝不能是本地时区偏移后的 ~8h
    sql_now = queue_db.execute("SELECT current_timestamp").fetchone()[0]
    next_run = datetime.strptime(task["next_run_at"], "%Y-%m-%d %H:%M:%S")
    now = datetime.strptime(sql_now, "%Y-%m-%d %H:%M:%S")
    wait_secs = (next_run - now).total_seconds()
    assert 50 <= wait_secs <= 70


def test_fail_at_max_attempts_marks_error(queue_db):
    """将 max_attempts 设为 1，第一次失败即标记 error。"""
    task_id = task_queue.enqueue("daily")
    queue_db.execute(
        "UPDATE sync_task_queue SET max_attempts=1 WHERE id=?", [task_id]
    )
    task_queue._claim_next()
    task_queue._fail(task_id, "persistent error")

    task = task_queue.get_task(task_id)
    assert task["status"] == "error"
    assert "persistent error" in task["last_error"]


# ─── cancel ──────────────────────────────────────────────────────────────────


def test_cancel_queued_task(queue_db):
    task_id = task_queue.enqueue("news")
    result = task_queue.cancel(task_id)

    assert result is True
    assert task_queue.get_task(task_id)["status"] == "cancelled"


def test_cancel_running_task_is_noop(queue_db):
    task_id = task_queue.enqueue("news")
    task_queue._claim_next()

    result = task_queue.cancel(task_id)

    assert result is False
    assert task_queue.get_task(task_id)["status"] == "running"


# ─── get_queue_status ────────────────────────────────────────────────────────


def test_queue_status_counts(queue_db):
    task_queue.enqueue("daily")
    task_queue.enqueue("news")
    task_queue._claim_next()   # 认领第一个 → running

    status = task_queue.get_queue_status()

    assert status["counts"].get("queued", 0) == 1
    assert status["counts"].get("running", 0) == 1
    assert len(status["recent"]) >= 2


# ─── get_history ─────────────────────────────────────────────────────────────


def test_get_history_returns_all(queue_db):
    task_queue.enqueue("daily", dedup=False)
    task_queue.enqueue("news", dedup=False)

    history = task_queue.get_history(limit=10)
    assert len(history) == 2


def test_get_history_filters_by_type(queue_db):
    task_queue.enqueue("daily", dedup=False)
    task_queue.enqueue("news", dedup=False)
    task_queue.enqueue("daily", dedup=False)

    history = task_queue.get_history(limit=10, task_type="daily")
    assert len(history) == 2
    assert all(t["task_type"] == "daily" for t in history)


def test_get_history_ordered_newest_first(queue_db):
    id1 = task_queue.enqueue("daily", dedup=False)
    id2 = task_queue.enqueue("news", dedup=False)

    history = task_queue.get_history(limit=10)
    assert history[0]["id"] == id2
    assert history[1]["id"] == id1


# ─── cleanup_history ───────────────────────────────────────────────────────


def test_cleanup_history_deletes_old_finished(queue_db):
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()
    task_queue._complete(task_id, {"synced": 1})
    old_ts = ts(_utc_now() - timedelta(days=60))
    queue_db.execute(
        "UPDATE sync_task_queue SET finished_at=? WHERE id=?",
        [old_ts, task_id],
    )

    result = task_queue.cleanup_history(retain_days=30, retain_count=0)
    assert result["deleted"] >= 1
    assert task_queue.get_task(task_id) is None


def test_cleanup_history_keeps_queued(queue_db):
    task_id = task_queue.enqueue("daily")
    result = task_queue.cleanup_history(retain_days=1, retain_count=0)
    assert task_queue.get_task(task_id) is not None
    assert result["deleted"] == 0


# ─── enqueue 去重 ────────────────────────────────────────────────────────────


def test_enqueue_dedup_skips_when_queued(queue_db):
    """同 job 已有 queued 任务时，dedup=True 应跳过入队，返回已存在 id。"""
    first = task_queue.enqueue("daily", payload={"days": 1})
    second = task_queue.enqueue("daily", payload={"days": 5})

    assert second == first  # 返回已存在的 id
    queued = get_sqlite_conn().execute(
        "SELECT COUNT(*) FROM sync_task_queue WHERE task_type='daily' AND status='queued'"
    ).fetchone()[0]
    assert queued == 1  # 只有一条


def test_enqueue_dedup_skips_when_running(queue_db):
    """同 job 已有 running 任务时也应跳过。"""
    first = task_queue.enqueue("daily")
    task_queue._claim_next()  # 标记 running
    second = task_queue.enqueue("daily")

    assert second == first
    running = get_sqlite_conn().execute(
        "SELECT COUNT(*) FROM sync_task_queue WHERE task_type='daily' AND status='running'"
    ).fetchone()[0]
    assert running == 1


def test_enqueue_dedup_bypass(queue_db):
    """dedup=False 强制入队，允许同 job 多任务排队（手动重跑场景）。"""
    first = task_queue.enqueue("daily")
    second = task_queue.enqueue("daily", dedup=False)

    assert second != first
    queued = get_sqlite_conn().execute(
        "SELECT COUNT(*) FROM sync_task_queue WHERE task_type='daily' AND status='queued'"
    ).fetchone()[0]
    assert queued == 2


def test_enqueue_dedup_different_jobs(queue_db):
    """不同 job 互不去重。"""
    a = task_queue.enqueue("daily")
    b = task_queue.enqueue("news")
    assert a != b
    assert task_queue.get_task(a)["task_type"] == "daily"
    assert task_queue.get_task(b)["task_type"] == "news"


# ─── row enrichment ──────────────────────────────────────────────────────────


def test_row_to_dict_includes_duration_and_records(queue_db):
    task_id = task_queue.enqueue("daily")
    task_queue._claim_next()
    task_queue._complete(task_id, {"synced": 99})
    task = task_queue.get_task(task_id)
    assert task.get("records") == 99
    assert task.get("job") == "daily"


# ─── backfill window bypass ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_daily_backfill_skips_window(monkeypatch, db_conn):
    from xshare.data.sync_config import init_sync_config
    from xshare.data import sync_config

    init_sync_config()
    monkeypatch.setenv("TUSHARE_TOKEN", "fake")
    monkeypatch.setattr(
        sync_config, "_daily_sync_window_open", lambda now=None: False
    )
    handlers = dict(task_queue._BLOCKING_HANDLERS)
    handlers["daily"] = lambda payload=None: 100
    monkeypatch.setattr(task_queue, "_BLOCKING_HANDLERS", handlers)

    result = await task_queue.run_job("daily", payload={"backfill": True, "days": 252})
    assert result["status"] == "ok"
    assert result["synced"] == 100


@pytest.mark.asyncio
async def test_run_job_daily_without_backfill_respects_window(monkeypatch, db_conn):
    from xshare.data.sync_config import init_sync_config
    from xshare.data import sync_config

    init_sync_config()
    monkeypatch.setattr(
        sync_config, "_daily_sync_window_open", lambda now=None: False
    )

    result = await task_queue.run_job("daily", payload={"days": 1})
    assert result["status"] == "skipped"


# ─── sync_job tool 集成 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_job_enqueue_action(db_conn):
    from xshare.data.sync_config import init_sync_config
    from xshare.tools import sync_job as sj_tool

    init_sync_config()

    resp = await sj_tool.sync_job({"action": "enqueue", "job": "daily", "days": 3})
    data = json.loads(resp)

    assert "queued" in data
    assert data["queued"][0]["job"] == "daily"
    task_id = data["queued"][0]["task_id"]
    task = task_queue.get_task(task_id)
    assert task is not None
    assert task["payload"]["days"] == 3
    assert task["trigger"] == "manual"


@pytest.mark.asyncio
async def test_sync_job_enqueue_multi_year_single_code(db_conn):
    from xshare.data.sync_config import init_sync_config
    from xshare.tools import sync_job as sj_tool

    init_sync_config()
    resp = await sj_tool.sync_job({
        "action": "enqueue", "job": "index_daily",
        "code": "000300.SH", "years": 5,
    })
    data = json.loads(resp)
    task = task_queue.get_task(data["queued"][0]["task_id"])
    assert task["payload"] == {"years": 5, "code": "000300.SH"}


@pytest.mark.asyncio
async def test_sync_job_rejects_invalid_years(db_conn):
    from xshare.tools import sync_job as sj_tool

    resp = await sj_tool.sync_job({"action": "enqueue", "job": "daily", "years": 0})
    assert "error" in json.loads(resp)


@pytest.mark.asyncio
async def test_sync_job_history_action(db_conn):
    from xshare.data.sync_config import init_sync_config
    from xshare.tools import sync_job as sj_tool

    init_sync_config()
    task_queue.enqueue("news", dedup=False)

    resp = await sj_tool.sync_job({"action": "history", "job": "all"})
    data = json.loads(resp)

    assert "history" in data
    assert len(data["history"]) >= 1


@pytest.mark.asyncio
async def test_sync_job_status_includes_queue(db_conn):
    from xshare.data.sync_config import init_sync_config
    from xshare.tools import sync_job as sj_tool

    init_sync_config()

    resp = await sj_tool.sync_job({"action": "status"})
    data = json.loads(resp)

    assert "jobs" in data
    assert "queue" in data
    assert "counts" in data["queue"]
