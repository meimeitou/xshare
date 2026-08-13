"""持久化异步任务队列 + 任务执行器。

统一管理来自 CLI、定时调度器、MCP 工具的同步任务，由后台 worker 单通道消费。
任务执行（run_job）也在此模块：worker 认领任务后直接调用 run_job，无需跨模块
反向引用，依赖方向单向 sync_config → task_queue（sync_loop 调 enqueue）。

状态机：queued → running → success | error | cancelled
失败时按指数退避重入队（最多 max_attempts 次）。

公开接口
--------
enqueue(task_type, payload, priority, trigger) → task_id
cancel(task_id) → bool
get_task(task_id) → dict | None
get_queue_status() → dict
get_history(limit, task_type) → list[dict]
cleanup_history(retain_days, retain_count) → dict
run_job(job, payload) → dict            ← 执行单个任务（worker 调用）
run_workers()             ← asyncio.Task，在 MCP Server 内运行（多 worker 并发）
enqueue_initial_jobs()     ← 启动时对启用 job 各入队一次（system trigger）
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from xshare.data.sqlite_db import get_sqlite_conn, now_ts, sqlite_critical, ts

logger = logging.getLogger(__name__)

# 任务类型与 sync_config.ALL_JOBS 同源（单向依赖：此处 import，sync_config 不反向 import 本模块）
from xshare.data.sync_config import (
    ALL_JOBS as VALID_TASK_TYPES,
    CALENDAR_JOBS,
    DAILY_BASIC_JOB,
    DAILY_JOB,
    ETF_BASIC_JOB,
    FINANCE_JOB,
    FUND_DAILY_JOB,
    FUND_NAV_JOB,
    INDEX_BASIC_JOB,
    INDEX_DAILY_JOB,
    NEWS_JOB,
    QUOTE_JOB,
    STOCK_JOB,
    TRADE_CAL_JOB,
    _BLOCKING_HANDLERS,
    _set_state,
    check_calendar_window,
    get_all,
)
from xshare.utils import env_int


def _utc_now() -> datetime:
    """当前 UTC 朴素 datetime，与 SQL current_timestamp 同坐标系。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

STATUS_QUEUED    = "queued"
STATUS_RUNNING   = "running"
STATUS_SUCCESS   = "success"
STATUS_SKIPPED   = "skipped"
STATUS_ERROR     = "error"
STATUS_CANCELLED = "cancelled"

# running 任务超过此秒数无心跳则视为僵尸，重新入队
_LEASE_TTL = 300   # 5 分钟
# worker 空闲时的轮询间隔（秒）
_DEFAULT_POLL = 5.0


# ─── 公开接口 ─────────────────────────────────────────────────────────────────


def enqueue(
    task_type: str,
    payload: dict | None = None,
    priority: int = 5,
    trigger: str = "manual",
    dedup: bool = True,
) -> int:
    """将任务写入队列，返回 task_id。

    Parameters
    ----------
    task_type:  'daily' | 'news' | 'stock_basic'
    payload:    任务参数，如 {"days": 5}
    priority:   1 最高，10 最低（默认 5）
    trigger:    'manual' | 'schedule' | 'system'
    dedup:      True 时若同 task_type 已有 queued/running 任务则跳过入队，
                返回已存在任务的 id（无则返回 0）。避免调度器反复入队导致
                队列堆积。手动强制重跑可传 False 绕过。
    """
    if task_type not in VALID_TASK_TYPES:
        raise ValueError(f"未知任务类型: {task_type}，可选: {VALID_TASK_TYPES}")
    conn = get_sqlite_conn()
    if dedup:
        existing = conn.execute(
            """
            SELECT id FROM sync_task_queue
            WHERE task_type=? AND status IN ('queued','running')
            ORDER BY id DESC LIMIT 1
            """,
            [task_type],
        ).fetchone()
        if existing:
            existing_id = int(existing[0])
            logger.info(
                "[sync] 任务去重跳过 type=%s trigger=%s，已有 #%d 在队",
                task_type, trigger, existing_id,
            )
            return existing_id
    row = conn.execute(
        """
        INSERT INTO sync_task_queue (task_type, payload, status, priority, trigger)
        VALUES (?, ?, 'queued', ?, ?)
        RETURNING id
        """,
        [task_type, json.dumps(payload or {}, ensure_ascii=False), priority, trigger],
    ).fetchone()
    task_id = int(row[0])
    logger.info(
        "[sync] 任务已入队 #%d type=%s trigger=%s priority=%s payload=%s",
        task_id, task_type, trigger, priority, payload,
    )
    return task_id


def cancel(task_id: int) -> bool:
    """取消排队中的任务。running / 已完成状态无法取消，返回 False。"""
    conn = get_sqlite_conn()
    conn.execute(
        "UPDATE sync_task_queue SET status='cancelled', finished_at=current_timestamp "
        "WHERE id=? AND status='queued'",
        [task_id],
    )
    row = conn.execute(
        "SELECT status FROM sync_task_queue WHERE id=?", [task_id]
    ).fetchone()
    return row is not None and row[0] == STATUS_CANCELLED


def get_task(task_id: int) -> dict | None:
    """按 task_id 查询任务详情。"""
    conn = get_sqlite_conn()
    row = conn.execute(
        "SELECT id, task_type, payload, status, priority, trigger, attempts, "
        "max_attempts, queued_at, started_at, finished_at, next_run_at, result, last_error "
        "FROM sync_task_queue WHERE id=?",
        [task_id],
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_queue_status() -> dict:
    """各状态任务计数 + 最近 10 条记录（倒序）。"""
    conn = get_sqlite_conn()
    counts: dict[str, int] = {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT status, COUNT(*) FROM sync_task_queue GROUP BY status"
        ).fetchall()
    }
    recent = _fetch_tasks(conn, limit=10)
    return {"counts": counts, "recent": recent}


def get_history(limit: int = 20, task_type: str | None = None) -> list[dict]:
    """查询任务历史（倒序）。"""
    conn = get_sqlite_conn()
    return _fetch_tasks(conn, limit=limit, task_type=task_type)


def cleanup_history(*, retain_days: int = 30, retain_count: int = 500) -> dict:
    """清理已完成任务日志。不删除 queued / running。

    删除 finished_at 早于 retain_days 的记录，但每种 task_type 至少保留 retain_count 条最新记录。
    """
    retain_days = max(1, int(retain_days))
    retain_count = max(0, int(retain_count))
    cutoff = ts(_utc_now() - timedelta(days=retain_days))
    conn = get_sqlite_conn()
    finished_statuses = (STATUS_SUCCESS, STATUS_SKIPPED, STATUS_ERROR, STATUS_CANCELLED)

    protected: set[int] = set()
    if retain_count > 0:
        for task_type in VALID_TASK_TYPES:
            rows = conn.execute(
                """
                SELECT id FROM sync_task_queue
                WHERE task_type=? AND status IN (?,?,?,?)
                ORDER BY id DESC LIMIT ?
                """,
                [task_type, *finished_statuses, retain_count],
            ).fetchall()
            protected.update(int(r[0]) for r in rows)

    placeholders = ",".join("?" * len(finished_statuses))
    params: list = list(finished_statuses) + [cutoff]
    sql = (
        f"SELECT id FROM sync_task_queue "
        f"WHERE status IN ({placeholders}) AND finished_at IS NOT NULL AND finished_at < ?"
    )
    if protected:
        sql += f" AND id NOT IN ({','.join('?' * len(protected))})"
        params.extend(sorted(protected))

    to_delete = [int(r[0]) for r in conn.execute(sql, params).fetchall()]
    if to_delete:
        conn.execute(
            f"DELETE FROM sync_task_queue WHERE id IN ({','.join('?' * len(to_delete))})",
            to_delete,
        )
    logger.info("任务日志清理: 删除 %d 条（保留 %d 天，每类至少 %d 条）", len(to_delete), retain_days, retain_count)
    return {"deleted": len(to_delete), "retain_days": retain_days, "retain_count": retain_count}


# ─── 内部操作 ─────────────────────────────────────────────────────────────────


def _claim_next() -> dict | None:
    """认领下一个 queued 任务，标记为 running，返回任务字典；无任务时返回 None。

    同时回收超时僵尸任务（running 但 lease_at 过期）。

    整个回收 + 选取 + 标记 running 在单个 ``BEGIN IMMEDIATE`` 事务内完成，
    并由 ``sqlite_critical`` 持有连接锁，保证抢任务的原子性（未来多 worker 也安全）。
    """
    # cutoff 必须用 UTC，与 SQL current_timestamp / lease_at 同坐标系
    cutoff = ts(_utc_now() - timedelta(seconds=_LEASE_TTL))
    with sqlite_critical() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 回收僵尸
            conn.execute(
                "UPDATE sync_task_queue "
                "SET status='queued', started_at=NULL, lease_at=NULL "
                "WHERE status='running' AND lease_at IS NOT NULL AND lease_at < ?",
                [cutoff],
            )
            # 取优先级最高（数字最小）、最早入队的就绪任务
            row = conn.execute(
                "SELECT id FROM sync_task_queue "
                "WHERE status='queued' "
                "  AND (next_run_at IS NULL OR next_run_at <= current_timestamp) "
                "ORDER BY priority ASC, id ASC "
                "LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            task_id = int(row[0])
            conn.execute(
                "UPDATE sync_task_queue "
                "SET status='running', started_at=current_timestamp, "
                "    lease_at=current_timestamp, attempts=attempts+1 "
                "WHERE id=?",
                [task_id],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_task(task_id)


def _renew_lease(task_id: int) -> None:
    """更新 worker 心跳，防止被误判为僵尸。"""
    get_sqlite_conn().execute(
        "UPDATE sync_task_queue SET lease_at=current_timestamp WHERE id=?",
        [task_id],
    )


def _complete(task_id: int, result: dict) -> None:
    """标记任务成功，写入执行结果。"""
    get_sqlite_conn().execute(
        "UPDATE sync_task_queue "
        "SET status='success', finished_at=current_timestamp, result=?, last_error=NULL "
        "WHERE id=?",
        [json.dumps(result, ensure_ascii=False, default=str), task_id],
    )


def _skip(task_id: int, result: dict) -> None:
    """标记任务跳过（窗口外 / 缺 token 等），不重试。"""
    reason = result.get("reason")
    get_sqlite_conn().execute(
        "UPDATE sync_task_queue "
        "SET status='skipped', finished_at=current_timestamp, result=?, last_error=? "
        "WHERE id=?",
        [json.dumps(result, ensure_ascii=False, default=str), reason, task_id],
    )


def _fail(task_id: int, error: str) -> None:
    """标记任务失败。未达最大重试次数时按指数退避重入队，否则标记 error。

    退避策略：第 1 次 60s，第 2 次 120s，第 3 次 600s（上限 10 分钟）。
    next_run_at 用与 SQL current_timestamp 同格式的 UTC 字符串。
    """
    conn = get_sqlite_conn()
    row = conn.execute(
        "SELECT attempts, max_attempts FROM sync_task_queue WHERE id=?", [task_id]
    ).fetchone()
    if not row:
        return
    attempts, max_att = int(row[0]), int(row[1])
    if attempts >= max_att:
        conn.execute(
            "UPDATE sync_task_queue "
            "SET status='error', finished_at=current_timestamp, last_error=? "
            "WHERE id=?",
            [error, task_id],
        )
        logger.warning("任务 #%d 已达最大重试次数 %d，标记为 error", task_id, max_att)
    else:
        delay = min(60 * (2 ** (attempts - 1)), 600)
        next_run = ts(_utc_now() + timedelta(seconds=delay))
        conn.execute(
            "UPDATE sync_task_queue "
            "SET status='queued', started_at=NULL, lease_at=NULL, "
            "    last_error=?, next_run_at=? "
            "WHERE id=?",
            [error, next_run, task_id],
        )
        logger.info(
            "任务 #%d 将在 %ds 后重试（第 %d/%d 次）",
            task_id, delay, attempts, max_att,
        )


def _apply_job_result(task_id: int, result: dict) -> None:
    """根据 run_job 返回的 status 更新队列：ok→success，skipped→跳过，error→重试/失败。

    run_job 内部吞掉异常并返回 dict，worker 必须按 status 分流，否则瞬时失败
    会被误记为 success、重试永不触发。
    """
    status = result.get("status")
    if status == "ok":
        _complete(task_id, result)
    elif status == "skipped":
        _skip(task_id, result)
    elif status == "error":
        _fail(task_id, str(result.get("error") or "unknown error"))
    else:
        _fail(task_id, f"unexpected job status: {status!r}")


def _fetch_tasks(conn, limit: int = 10, task_type: str | None = None) -> list[dict]:
    sql = (
        "SELECT id, task_type, payload, status, priority, trigger, attempts, "
        "max_attempts, queued_at, started_at, finished_at, next_run_at, result, last_error "
        "FROM sync_task_queue"
    )
    params: list = []
    if task_type:
        sql += " WHERE task_type=?"
        params.append(task_type)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def _row_to_dict(row) -> dict:
    keys = [
        "id", "task_type", "payload", "status", "priority", "trigger",
        "attempts", "max_attempts", "queued_at", "started_at", "finished_at",
        "next_run_at", "result", "last_error",
    ]
    d = dict(zip(keys, row))
    for k in ("payload", "result"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except (ValueError, TypeError):
                pass
    for k in ("queued_at", "started_at", "finished_at", "next_run_at"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    d["job"] = d.get("task_type")
    result = d.get("result")
    if isinstance(result, dict) and "synced" in result:
        d["records"] = result.get("synced")
    if d.get("started_at") and d.get("finished_at"):
        try:
            start = datetime.strptime(d["started_at"][:19], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(d["finished_at"][:19], "%Y-%m-%d %H:%M:%S")
            d["duration_s"] = max(0.0, (end - start).total_seconds())
        except (ValueError, TypeError):
            pass
    return d


# ─── 任务执行器 ───────────────────────────────────────────────────────────────


async def run_job(job: str, payload: dict | None = None) -> dict:
    """执行单个同步任务（worker 认领后调用，也供直接调用）。

    单通道 worker 已串行化执行，无需额外锁。状态回写由 _set_state 完成，
    阻塞抓取经 asyncio.to_thread 放线程池。
    """
    if job not in VALID_TASK_TYPES:
        raise ValueError(f"未知任务: {job}，可选: {VALID_TASK_TYPES}")

    started = now_ts()
    t0 = time.monotonic()
    handler = _BLOCKING_HANDLERS.get(job)
    if handler is None:
        raise ValueError(f"无执行器: {job}")

    logger.info("[sync] run_job 开始 job=%s payload=%s", job, payload or {})

    try:
        eligible, reason = check_calendar_window(job, payload)
        if not eligible:
            _set_state(job, "skipped", reason)
            logger.info(
                "[sync] run_job 跳过 job=%s reason=%s elapsed=%.2fs",
                job, reason, time.monotonic() - t0,
            )
            return {"job": job, "status": "skipped", "reason": reason, "started_at": started}

        needs_token = job not in (NEWS_JOB, QUOTE_JOB)
        if needs_token and not os.environ.get("TUSHARE_TOKEN"):
            reason = "TUSHARE_TOKEN 未配置"
            _set_state(job, "skipped", reason)
            logger.info(
                "[sync] run_job 跳过 job=%s reason=%s elapsed=%.2fs",
                job, reason, time.monotonic() - t0,
            )
            return {"job": job, "status": "skipped", "reason": reason, "started_at": started}

        count = await asyncio.to_thread(handler, payload)
        elapsed = time.monotonic() - t0
        _set_state(job, "ok")
        logger.info(
            "[sync] run_job 完成 job=%s status=ok synced=%s elapsed=%.2fs",
            job, count, elapsed,
        )
        return {"job": job, "status": "ok", "synced": count, "started_at": started, "elapsed_s": round(elapsed, 2)}
    except Exception as exc:
        err = str(exc)
        elapsed = time.monotonic() - t0
        _set_state(job, "error", err)
        logger.warning(
            "[sync] run_job 失败 job=%s error=%s elapsed=%.2fs",
            job, err, elapsed,
        )
        return {"job": job, "status": "error", "error": err, "started_at": started, "elapsed_s": round(elapsed, 2)}


def _env_int(name: str, default: int) -> int:
    """读取环境变量为正 int（<=0 回退 default）。"""
    v = env_int(name, default)
    return v if v > 0 else default


def _local_daily_empty(daily_table: str) -> bool:
    """对应日线表是否完全无数据。库空时启动不应自动跑 backfill，
    应由前端一次性补全接口显式触发。"""
    try:
        from xshare.data.db import get_conn
        conn = get_conn()
        return int(conn.execute(f"SELECT COUNT(*) FROM {daily_table}").fetchone()[0] or 0) == 0
    except Exception:
        return False


def enqueue_initial_jobs() -> list[int]:
    """启动时入队 system 任务。

    顺序：trade_cal -> stock/index/etf basic -> daily 类增量（窗口开放且库非空）
    -> daily_basic -> finance -> fund_nav -> news

    历史数据补全不再由启动自动触发：库空时日线类不入队，应由前端
    一次性补全接口（start_date/end_date + overwrite）显式触发。后续缺口
    由定时 _calendar_loop_iteration 的 watermark 缺口扫描自动补。
    """
    ids: list[int] = []
    configs = {c["job"]: c for c in get_all()}

    def _try_enqueue(job: str, **kwargs) -> None:
        cfg = configs.get(job)
        if not cfg or not cfg["enabled"]:
            return
        try:
            ids.append(enqueue(job, trigger="system", **kwargs))
        except Exception as exc:
            logger.warning("初次入队 %s 失败: %s", job, exc)

    _try_enqueue(TRADE_CAL_JOB, priority=1)
    _try_enqueue(STOCK_JOB, priority=2)
    _try_enqueue(INDEX_BASIC_JOB, priority=2)
    _try_enqueue(ETF_BASIC_JOB, priority=2)

    daily_cfg = configs.get(DAILY_JOB)
    if daily_cfg and daily_cfg["enabled"]:
        try:
            # 不再用覆盖率数量阈值自动触发 backfill；库空时由前端一次性补全接口触发。
            # 仅在当日窗口开放时入队 days=1 增量。
            if check_calendar_window(DAILY_JOB)[0] and not _local_daily_empty("stock_daily"):
                ids.append(
                    enqueue(DAILY_JOB, payload={"days": 1}, trigger="system", priority=5)
                )
                if configs.get(DAILY_BASIC_JOB, {}).get("enabled"):
                    ids.append(
                        enqueue(DAILY_BASIC_JOB, payload={"days": 1}, trigger="system", priority=6)
                    )
        except Exception as exc:
            logger.warning("初次入队 %s 失败: %s", DAILY_JOB, exc)

    index_daily_cfg = configs.get(INDEX_DAILY_JOB)
    if index_daily_cfg and index_daily_cfg["enabled"]:
        try:
            if check_calendar_window(INDEX_DAILY_JOB)[0] and not _local_daily_empty("index_daily"):
                ids.append(
                    enqueue(INDEX_DAILY_JOB, payload={"days": 1}, trigger="system", priority=5)
                )
        except Exception as exc:
            logger.warning("初次入队 %s 失败: %s", INDEX_DAILY_JOB, exc)

    fund_daily_cfg = configs.get(FUND_DAILY_JOB)
    if fund_daily_cfg and fund_daily_cfg["enabled"]:
        try:
            if check_calendar_window(FUND_DAILY_JOB)[0] and not _local_daily_empty("fund_daily"):
                ids.append(
                    enqueue(FUND_DAILY_JOB, payload={"days": 1}, trigger="system", priority=5)
                )
        except Exception as exc:
            logger.warning("初次入队 %s 失败: %s", FUND_DAILY_JOB, exc)

    _try_enqueue(FINANCE_JOB, priority=7)
    if check_calendar_window(FUND_NAV_JOB)[0]:
        _try_enqueue(FUND_NAV_JOB, priority=8)

    for cfg in get_all():
        job = cfg["job"]
        if job in (
            STOCK_JOB, DAILY_JOB, INDEX_BASIC_JOB, INDEX_DAILY_JOB,
            ETF_BASIC_JOB, FUND_DAILY_JOB,
            TRADE_CAL_JOB, DAILY_BASIC_JOB, FINANCE_JOB, FUND_NAV_JOB,
        ):
            continue
        if not cfg["enabled"]:
            continue
        try:
            ids.append(enqueue(job, trigger="system"))
        except Exception as exc:
            logger.warning("初次入队 %s 失败: %s", job, exc)
    return ids


# ─── 后台 Worker ──────────────────────────────────────────────────────────────


async def run_worker(poll_interval: float = _DEFAULT_POLL) -> None:
    """从队列消费并执行任务。作为 asyncio.Task 在 MCP Server 内运行。

    - 每 poll_interval 秒检查一次队列（有任务时立即执行，无任务时等待）。
    - 执行期间定期更新 lease_at 心跳，防止被僵尸回收逻辑误判。
    - run_job 已在本模块内，无循环依赖。

    单 worker 内串行 await；并发由 ``run_workers`` 启动多个本协程实现。
    """
    logger.info("[sync] 任务队列 worker 已启动（轮询间隔 %.1fs）", poll_interval)
    while True:
        try:
            task = _claim_next()
            if task is None:
                await asyncio.sleep(poll_interval)
                continue

            task_id = task["id"]
            task_type = task["task_type"]
            payload = task.get("payload") or {}
            trigger = task.get("trigger") or "manual"
            attempts = task.get("attempts") or 1
            max_attempts = task.get("max_attempts") or 3
            priority = task.get("priority")
            t0 = time.monotonic()

            logger.info(
                "[sync] 异步任务开始 #%d type=%s trigger=%s priority=%s "
                "attempt=%s/%s payload=%s",
                task_id, task_type, trigger, priority,
                attempts, max_attempts, payload,
            )

            heartbeat = asyncio.create_task(_heartbeat_loop(task_id))
            try:
                result = await run_job(task_type, payload=payload)
                _apply_job_result(task_id, result)
                elapsed = time.monotonic() - t0
                status = result.get("status", "?")
                if status == "ok":
                    logger.info(
                        "[sync] 异步任务成功 #%d type=%s synced=%s elapsed=%.2fs",
                        task_id, task_type, result.get("synced"), elapsed,
                    )
                elif status == "skipped":
                    logger.info(
                        "[sync] 异步任务跳过 #%d type=%s reason=%s elapsed=%.2fs",
                        task_id, task_type, result.get("reason"), elapsed,
                    )
                else:
                    logger.warning(
                        "[sync] 异步任务结束 #%d type=%s status=%s error=%s elapsed=%.2fs",
                        task_id, task_type, status, result.get("error"), elapsed,
                    )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                _fail(task_id, str(exc))
                logger.warning(
                    "[sync] 异步任务异常 #%d type=%s error=%s elapsed=%.2fs",
                    task_id, task_type, exc, elapsed,
                )
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            logger.info("[sync] 任务队列 worker 已停止")
            raise
        except Exception as exc:
            logger.exception("[sync] 任务队列 worker 内部异常: %s", exc)
            await asyncio.sleep(poll_interval)


def _worker_count() -> int:
    """并发 worker 数量。默认 5：长 backfill 占用部分 worker 时，news 等短任务
    仍可用其余 worker 执行。受全局 Tushare 限速约束，过大收益有限。
    可通过环境变量 ``XSHARE_SYNC_WORKERS`` 配置。"""
    try:
        n = int(os.environ.get("XSHARE_SYNC_WORKERS", "5") or "5")
    except (TypeError, ValueError):
        n = 5
    return max(1, n)


async def run_workers(poll_interval: float = _DEFAULT_POLL) -> None:
    """启动多个 worker 协程并发消费队列，全部退出时返回。

    供 ``spawn_sync_runtime`` 使用。每个 worker 独立轮询 ``_claim_next``，
    靠 SQLite ``BEGIN IMMEDIATE`` 保证认领原子性。
    """
    n = _worker_count()
    workers = [asyncio.create_task(run_worker(poll_interval)) for _ in range(n)]
    logger.info("[sync] 启动 %d 个并发 worker", n)
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


async def _heartbeat_loop(task_id: int) -> None:
    interval = max(_LEASE_TTL // 3, 10)
    while True:
        await asyncio.sleep(interval)
        _renew_lease(task_id)
