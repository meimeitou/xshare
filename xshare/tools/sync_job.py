"""定时同步任务管理工具（供 MCP 调用）

单一入口 sync_job，通过 action 区分：
  - status   : 查看所有任务的配置、运行状态、队列与水位
  - watermarks: 水位明细
  - config   : 修改间隔 / 启停（热生效，30s 内被 loop 感知）
  - enqueue  : 将任务写入队列，由后台 worker 统一执行
  - run      : enqueue 的别名（兼容 Web API / 旧文档）
  - history  : 查询任务队列的历史执行记录
  - coverage : 日线本地覆盖率
  - cancel   : 取消排队中的任务
  - cleanup  : 清理已完成任务日志
"""

import json
import os

from xshare.data.sync_config import (
    ALL_JOBS,
    get_all,
    update,
)


async def sync_job(args: dict) -> str:
    action = args.get("action", "status")
    job = args.get("job", "all")

    # ─── status: 查看配置、运行状态、队列与水位 ─────────────────────────────
    if action == "status":
        from xshare.data import rate_limit
        from xshare.data import watermark as wm
        from xshare.data.task_queue import get_queue_status
        return json.dumps(
            {
                "jobs": get_all(),
                "queue": get_queue_status(),
                "watermarks": wm.summarize(),
                "rate_limit": rate_limit.all_stats(),
            },
            ensure_ascii=False, default=str,
        )

    # ─── watermarks: 水位明细 ─────────────────────────────────────────────
    if action == "watermarks":
        from xshare.data import watermark as wm
        dataset = args.get("dataset")
        limit = int(args.get("limit", 50))
        return json.dumps(
            {
                "summary": wm.summarize(dataset),
                "rows": wm.list_watermarks(dataset, limit=limit),
            },
            ensure_ascii=False, default=str,
        )

    # ─── coverage: 日线覆盖率 ─────────────────────────────────────────────
    if action == "coverage":
        from xshare.data.sources.tushare_source import (
            get_daily_coverage,
            get_fund_daily_coverage,
            get_index_daily_coverage,
        )
        lookback = args.get("lookback_trading_days")
        if lookback is not None:
            lookback = int(lookback)
        return json.dumps(
            {
                "stock": get_daily_coverage(lookback),
                "index": get_index_daily_coverage(lookback),
                "fund": get_fund_daily_coverage(lookback),
            },
            ensure_ascii=False, default=str,
        )

    # ─── config: 修改间隔 / 启停 ──────────────────────────────────────────
    if action == "config":
        if job not in ALL_JOBS:
            return json.dumps(
                {"error": f"未知任务: {job}", "valid_jobs": list(ALL_JOBS), "retry_same_args": False},
                ensure_ascii=False,
            )
        enabled = args.get("enabled")
        interval_minutes = args.get("interval_minutes")
        if enabled is None and interval_minutes is None:
            return json.dumps(
                {"error": "config 动作需提供 enabled 或 interval_minutes", "retry_same_args": False},
                ensure_ascii=False,
            )
        try:
            updated = update(job, enabled=enabled, interval_minutes=interval_minutes)
        except ValueError as e:
            return json.dumps({"error": str(e), "retry_same_args": False}, ensure_ascii=False)
        return json.dumps(
            {"message": f"已更新 {job} 配置", "job": updated},
            ensure_ascii=False, default=str,
        )

    # ─── enqueue / run: 写入队列，由 worker 统一执行 ─────────────────────
    if action in ("enqueue", "run"):
        jobs_to_queue = list(ALL_JOBS) if job == "all" else [job]
        if job != "all" and job not in ALL_JOBS:
            return json.dumps(
                {"error": f"未知任务: {job}", "valid_jobs": list(ALL_JOBS), "retry_same_args": False},
                ensure_ascii=False,
            )
        from xshare.data.task_queue import enqueue
        if "years" in args:
            try:
                years = int(args["years"])
            except (TypeError, ValueError):
                return json.dumps({"error": "years 必须是正整数", "retry_same_args": False}, ensure_ascii=False)
            if years < 1 or years > 20:
                return json.dumps({"error": "years 必须在 1..20 之间", "retry_same_args": False}, ensure_ascii=False)
            if job not in ("daily", "index_daily", "fund_daily", "trade_cal"):
                return json.dumps({"error": f"任务 {job} 不支持 years", "retry_same_args": False}, ensure_ascii=False)
        # start_date/end_date/overwrite 仅日线类任务支持
        if any(k in args for k in ("start_date", "end_date", "overwrite")):
            if job not in ("daily", "index_daily", "fund_daily"):
                return json.dumps(
                    {"error": f"任务 {job} 不支持 start_date/end_date/overwrite", "retry_same_args": False},
                    ensure_ascii=False,
                )
        payload_keys = (
            "days", "years", "code", "pages", "retain_days",
            "backfill", "limit", "force",
            "start_date", "end_date", "overwrite",
        )
        payload = {k: args[k] for k in payload_keys if k in args} or None
        queued = []
        for j in jobs_to_queue:
            task_id = enqueue(j, payload=payload, trigger="manual")
            queued.append({"job": j, "task_id": task_id})
        return json.dumps(
            {"message": "任务已入队，将由后台 worker 执行", "queued": queued},
            ensure_ascii=False,
        )

    # ─── cancel: 取消排队任务 ─────────────────────────────────────────────
    if action == "cancel":
        task_id = args.get("task_id")
        if task_id is None:
            return json.dumps({"error": "cancel 需提供 task_id", "retry_same_args": False}, ensure_ascii=False)
        from xshare.data.task_queue import cancel, get_task
        ok = cancel(int(task_id))
        task = get_task(int(task_id))
        if not ok:
            return json.dumps(
                {"error": "无法取消（任务不存在或非 queued 状态）", "task": task, "retry_same_args": False},
                ensure_ascii=False, default=str,
            )
        return json.dumps(
            {"message": f"任务 #{task_id} 已取消", "task": task},
            ensure_ascii=False, default=str,
        )

    # ─── cleanup: 清理历史日志 ────────────────────────────────────────────
    if action == "cleanup":
        from xshare.data.task_queue import cleanup_history
        retain_days = int(args.get("retain_days") or os.environ.get("XSHARE_SYNC_HISTORY_RETAIN_DAYS", "30"))
        retain_count = int(args.get("retain_count", 500))
        result = cleanup_history(retain_days=retain_days, retain_count=retain_count)
        return json.dumps(
            {"message": f"已清理 {result['deleted']} 条任务日志", **result},
            ensure_ascii=False,
        )

    # ─── history: 查询队列历史 ────────────────────────────────────────────
    if action == "history":
        from xshare.data.task_queue import get_history
        limit = int(args.get("limit", 20))
        task_type = job if job != "all" else None
        history = get_history(limit=limit, task_type=task_type)
        return json.dumps({"history": history}, ensure_ascii=False, default=str)

    return json.dumps(
        {
            "error": f"未知 action: {action}",
            "valid_actions": [
                "status", "config", "enqueue", "run", "history",
                "coverage", "watermarks", "cancel", "cleanup",
            ],
            "retry_same_args": False,
        },
        ensure_ascii=False,
    )
