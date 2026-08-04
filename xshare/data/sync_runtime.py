"""启动/停止同步后台任务（worker + 定时 loop）。

MCP Server 与 FastAPI Web 共用此入口，避免一边有定时器、一边只有 worker。
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def spawn_sync_runtime() -> list[asyncio.Task]:
    """启动队列 worker；若 ``XSHARE_AUTO_SYNC!=0`` 再启定时 loop 与初次入队。

    始终启动 worker，保证手动 enqueue（Web UI / sync_job）可被消费。
    """
    from xshare.data.sync_config import ALL_JOBS, get_all, sync_loop
    from xshare.data.task_queue import enqueue_initial_jobs, run_workers

    tasks: list[asyncio.Task] = [asyncio.create_task(run_workers())]
    auto_sync = os.environ.get("XSHARE_AUTO_SYNC", "1") != "0"
    if not auto_sync:
        logger.info("XSHARE_AUTO_SYNC=0：仅启动 worker，不跑定时同步")
        return tasks

    for tid in enqueue_initial_jobs():
        logger.info("初次同步任务已入队: #%d", tid)
    retain = os.environ.get("XSHARE_SYNC_HISTORY_RETAIN_DAYS", "30")
    try:
        retain_days = int(retain)
        if retain_days > 0:
            from xshare.data.task_queue import cleanup_history
            result = cleanup_history(retain_days=retain_days, retain_count=500)
            if result["deleted"]:
                logger.info("启动时清理任务日志: %d 条", result["deleted"])
    except (TypeError, ValueError):
        pass

    for job in ALL_JOBS:
        tasks.append(asyncio.create_task(sync_loop(job)))
    for cfg in get_all():
        logger.info(
            "后台同步任务 %s: schedule=%s interval=%d enabled=%s",
            cfg["job"],
            cfg.get("schedule", "interval"),
            cfg["interval_minutes"],
            cfg["enabled"],
        )
    return tasks


async def shutdown_sync_runtime(tasks: list[asyncio.Task]) -> None:
    """取消后台 worker / sync_loop；吞掉已关闭 loop 上的噪音异常。"""
    if not tasks:
        return
    for t in tasks:
        if not t.done():
            t.cancel()
    try:
        await asyncio.wait(tasks, timeout=5.0)
    except (RuntimeError, asyncio.CancelledError) as exc:
        # Ctrl+C 二次按下时 loop 可能已关闭
        logger.debug("shutdown_sync_runtime wait: %s", exc)
    pending = [t for t in tasks if not t.done()]
    if pending:
        logger.warning("[sync] 仍有 %d 个后台任务未退出，强制丢弃", len(pending))
        for t in pending:
            t.cancel()
    else:
        logger.info("[sync] 后台同步任务已全部停止")
