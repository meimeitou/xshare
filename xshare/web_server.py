"""XShare Web Server - FastAPI REST wrapper around MCP tool functions

用法:
  xshare web [--port 8080] [--host 127.0.0.1]

Endpoints 一览:
  GET  /api/market/overview
  GET  /api/market/mainline
  GET  /api/market/top-movers?top_n=5
  GET  /api/market/sectors?top_n=5
  GET  /api/market/mainline-stocks?strong_limit=10&sector_top_n=8
  GET  /api/stock/resolve?q=<query>
  GET  /api/stock/list?q=&type=&limit=&offset=
  GET  /api/stock/{code}/quote
  GET  /api/stock/{code}/indicators?indicators=MA,MACD&period=daily
  GET  /api/stock/{code}/fundamentals
  GET  /api/stock/{code}/news?days=7
  GET  /api/portfolio
  POST /api/portfolio          body: {action, code, price, quantity, trade_date?, memo?}
  DELETE /api/portfolio/{id}
  GET  /api/sync/jobs
  GET  /api/sync/coverage
  GET  /api/sync/watermarks
  POST /api/sync/jobs/{job}/run
  POST /api/sync/jobs/{job}/enqueue   body: {days?, pages?, retain_days?, backfill?, limit?, force?, years?}
  POST /api/sync/jobs/all/enqueue
  GET  /api/sync/history?job=&limit=
  POST /api/sync/history/cleanup      body: {retain_days?, retain_count?}
  GET  /api/sync/tasks/{id}
  POST /api/sync/tasks/{id}/cancel
  PATCH /api/sync/jobs/{job}/config  body: {enabled?, interval_minutes?}
  POST /api/ai/chat              body: {session_id, message}  (SSE 流式)
  GET  /api/ai/sessions
  GET  /api/ai/sessions/{session_id}/history
  DELETE /api/ai/sessions/{session_id}
  GET  /api/health

API 文档: /docs（本地 Swagger UI，不依赖外网 CDN）
"""

import json
import logging
import os
import asyncio

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from swagger_ui_bundle import swagger_ui_path

from sse_starlette.sse import EventSourceResponse
from xshare.ai.agent import LLM_API_KEY, chat_stream

logger = logging.getLogger(__name__)
_queue_tasks: list[asyncio.Task] = []

_OPENAPI_TAGS = [
    {"name": "Market", "description": "大盘概览、主线、涨跌幅、板块"},
    {"name": "Stock", "description": "标的列表、解析、行情、指标、基本面、新闻"},
    {"name": "Portfolio", "description": "持仓汇总与交易记录"},
    {"name": "Sync", "description": "同步任务状态、入队、水位、历史"},
    {"name": "AI", "description": "AI 问股对话"},
    {"name": "Health", "description": "服务健康检查"},
]


def _cors_allow_origins() -> list[str]:
    """Return CORS allowed origins from env or safe local defaults."""
    raw = os.getenv("XSHARE_CORS_ALLOW_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    return [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:5001",
    ]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动/关闭 sync worker，替代已弃用的 on_event。"""
    global _queue_tasks
    from xshare.logging_config import configure_logging
    from xshare.data.db import close, init_tables
    from xshare.data.sqlite_db import close_sqlite, init_sqlite_tables
    from xshare.data.sync_config import init_sync_config
    from xshare.data.sync_runtime import shutdown_sync_runtime, spawn_sync_runtime

    configure_logging()
    init_tables()
    init_sqlite_tables()
    init_sync_config()
    _queue_tasks = spawn_sync_runtime()
    logger.info("Web API sync runtime started (%d background tasks)", len(_queue_tasks))
    try:
        yield
    finally:
        try:
            await shutdown_sync_runtime(_queue_tasks)
        except Exception as exc:
            logger.debug("sync runtime shutdown: %s", exc)
        _queue_tasks = []
        try:
            close()
            close_sqlite()
        except Exception as exc:
            logger.debug("db close on shutdown: %s", exc)


app = FastAPI(
    title="XShare Web API",
    version="0.1.0",
    description="XShare 金融分析 REST API（MCP 工具的 HTTP 包装）。Swagger：`/docs`。",
    openapi_tags=_OPENAPI_TAGS,
    docs_url=None,
    lifespan=_lifespan,
)
# swagger-ui-bundle 4.x 只认 openapi 3.0.n；FastAPI 默认 3.1.0 会报 version field 错误
app.openapi_version = "3.0.2"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    # Keep local dev flexible across random ports, e.g. 3000/5001/5173.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# 本地托管 Swagger UI，避免默认 CDN 在国内空白页
app.mount("/static/swagger", StaticFiles(directory=str(swagger_ui_path)), name="swagger-ui")


@app.get("/docs", include_in_schema=False)
async def swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css",
        swagger_favicon_url="/static/swagger/favicon-32x32.png",
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """工具层未捕获异常时仍返回 JSON，避免前端收到 HTML 500。"""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "path": request.url.path,
            "retry_same_args": False,
        },
    )


def _parse(raw: str) -> dict | list:
    """Parse JSON string returned by tool functions."""
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _run_tool_sync(tool_fn, args: dict) -> str:
    """在线程池执行 tool（多数 async tool 内部仍是阻塞 I/O）。

    uvicorn 单 worker 时，若在 async 路由里直接 await 这类 tool，会占满事件循环，
    导致其他请求（含 /api/health）一直 pending。
    """
    if asyncio.iscoroutinefunction(tool_fn):
        return asyncio.run(tool_fn(args))
    result = tool_fn(args)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


async def _invoke_tool(tool_fn, args: dict | None = None) -> str:
    return await asyncio.to_thread(_run_tool_sync, tool_fn, args or {})


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

@app.get("/api/market/overview", tags=["Market"], summary="大盘概览")
async def market_overview_endpoint():
    from xshare.tools.market_overview import market_overview
    return _parse(await _invoke_tool(market_overview, {}))


@app.get("/api/market/mainline", tags=["Market"], summary="市场主线")
async def market_mainline_endpoint():
    from xshare.tools.market_mainline import market_mainline
    return _parse(await _invoke_tool(market_mainline, {}))


@app.get("/api/market/top-movers", tags=["Market"], summary="涨跌幅榜")
async def market_top_movers_endpoint(top_n: int = Query(5, ge=1, le=50)):
    from xshare.tools.market_top_movers import market_top_movers
    return _parse(await _invoke_tool(market_top_movers, {"top_n": top_n}))


@app.get("/api/market/sectors", tags=["Market"], summary="板块涨跌")
async def market_sectors_endpoint(top_n: int = Query(5, ge=1, le=50)):
    from xshare.tools.market_sectors import market_sectors
    return _parse(await _invoke_tool(market_sectors, {"top_n": top_n}))


@app.get("/api/market/mainline-stocks", tags=["Market"], summary="主线强势股")
async def market_mainline_stocks_endpoint(
    strong_limit: int = Query(10, ge=1, le=50),
    sector_top_n: int = Query(8, ge=1, le=30),
):
    from xshare.tools.market_mainline import market_mainline
    return _parse(await _invoke_tool(market_mainline, {
        "strong_limit": strong_limit,
        "sector_top_n": sector_top_n,
    }))


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

@app.get("/api/stock/list", tags=["Stock"], summary="标的列表")
async def stock_list_endpoint(
    q: str = Query("", description="代码或名称过滤"),
    type: str | None = Query(None, alias="type"),
    sync_status: str | None = Query(
        None,
        description="同步状态过滤: ok / error / partial / pending / unsynced",
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if type not in (None, "stock", "etf", "index"):
        raise HTTPException(status_code=422, detail="type must be stock, etf, or index")
    if sync_status and sync_status not in ("ok", "error", "partial", "pending", "unsynced"):
        raise HTTPException(
            status_code=422,
            detail="sync_status must be ok, error, partial, pending, or unsynced",
        )

    from xshare.data.db import get_conn

    def query():
        where = ""
        params: list = []
        if q.strip():
            where = "WHERE code ILIKE ? OR name ILIKE ?"
            needle = f"%{q.strip()}%"
            params.extend([needle, needle])
        if type:
            where += (" AND " if where else "WHERE ") + "asset_type = ?"
            params.append(type)
        conn = get_conn()
        base = """
                SELECT code, name, 'stock' asset_type, '股票' asset_type_label,
                       market, industry, NULL index_name, NULL publisher, NULL category,
                       NULL exchange, list_date
                FROM stock_basic
                UNION ALL
                SELECT code, name, 'etf', 'ETF', NULL, NULL, index_name, NULL, NULL,
                       exchange, list_date FROM etf_basic
                UNION ALL
                SELECT code, name, 'index', '指数', market, NULL, NULL, publisher,
                       category, NULL, list_date FROM index_basic
            """
        # 资产类型 → 日线数据集名（对应 sync_watermark.dataset）
        # latest_trade_date 对应水位 key；同步状态反映该资产最新交易日数据是否入库成功。
        # sync_status 过滤在外层 final 上执行（因为它来自 JOIN）。
        final_cte = f"""WITH base AS ({base}),
                counts AS (
                    SELECT code, data_count, first_trade_date, latest_trade_date,
                           COALESCE(has_one_year_data, FALSE) AS has_one_year_data
                    FROM code_meta
                ), filtered AS (SELECT * FROM base {where}),
                final AS (
                    SELECT f.code, f.name, f.asset_type, f.asset_type_label,
                           f.market, f.industry, f.index_name, f.publisher, f.category,
                           f.exchange, f.list_date,
                           COALESCE(c.data_count, 0) data_count, c.first_trade_date,
                           c.latest_trade_date, COALESCE(c.has_one_year_data, FALSE) AS has_one_year_data,
                           w.status AS sync_status,
                           CAST(w.last_success_at AS VARCHAR) AS last_sync_at
                    FROM filtered f
                    LEFT JOIN counts c USING (code)
                    LEFT JOIN sync_watermark w
                      ON w.dataset = CASE f.asset_type
                            WHEN 'stock' THEN 'daily'
                            WHEN 'etf' THEN 'fund_daily'
                            WHEN 'index' THEN 'index_daily'
                       END
                     AND w.key = CAST(c.latest_trade_date AS VARCHAR)
                )"""
        status_where = ""
        if sync_status == "unsynced":
            status_where = "WHERE sync_status IS NULL"
        elif sync_status:
            status_where = "WHERE sync_status = ?"
            params = params + [sync_status]
        list_sql = f"{final_cte} SELECT * FROM final {status_where} ORDER BY asset_type, code LIMIT ? OFFSET ?"
        result = conn.execute(list_sql, params + [limit, offset])
        rows = result.fetchall()
        cols = [d[0] for d in result.description]
        count_sql = f"{final_cte} SELECT COUNT(*) FROM final {status_where}"
        total = conn.execute(count_sql, params).fetchone()[0]
        items = [dict(zip(cols, row)) for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset}
    return await asyncio.to_thread(query)

@app.get("/api/stock/resolve", tags=["Stock"], summary="模糊解析标的代码")
async def stock_resolve_endpoint(q: str = Query(..., description="模糊搜索关键词")):
    from xshare.tools.stock_resolve import stock_resolve
    return _parse(await _invoke_tool(stock_resolve, {"query": q}))


@app.get("/api/stock/{code}/quote", tags=["Stock"], summary="实时/最新行情")
async def stock_quote_endpoint(code: str):
    from xshare.tools.stock_quote import stock_quote
    return _parse(await _invoke_tool(stock_quote, {"code": code}))


@app.get("/api/stock/{code}/indicators", tags=["Stock"], summary="技术指标")
async def stock_indicators_endpoint(
    code: str,
    indicators: str = Query("MA,MACD,RSI", description="逗号分隔的指标列表"),
    period: str = Query("daily", description="行情周期: daily/weekly/monthly"),
):
    from xshare.tools.stock_indicators import stock_indicators
    return _parse(await _invoke_tool(stock_indicators, {
        "code": code,
        "indicators": [i.strip() for i in indicators.split(",") if i.strip()],
        "period": period,
    }))


@app.get("/api/stock/{code}/fundamentals", tags=["Stock"], summary="基本面数据")
async def stock_fundamentals_endpoint(code: str):
    from xshare.tools.stock_fundamentals import stock_fundamentals
    return _parse(await _invoke_tool(stock_fundamentals, {"code": code}))


@app.get("/api/stock/{code}/news", tags=["Stock"], summary="个股新闻")
async def stock_news_endpoint(code: str, days: int = Query(7, ge=1, le=90)):
    from xshare.tools.stock_news import stock_news
    return _parse(await _invoke_tool(stock_news, {"code": code, "days": days}))


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

class PortfolioAction(BaseModel):
    action: str = "buy"
    code: str | None = None
    price: float | None = None
    quantity: int | None = None
    trade_date: str | None = None
    memo: str | None = None


@app.get("/api/portfolio", tags=["Portfolio"], summary="持仓汇总")
async def portfolio_summary_endpoint():
    from xshare.tools.portfolio import portfolio_summary
    return _parse(await _invoke_tool(portfolio_summary, {}))


@app.post("/api/portfolio", tags=["Portfolio"], summary="新增/更新持仓交易")
async def portfolio_update_endpoint(body: PortfolioAction):
    from xshare.tools.portfolio import portfolio_update
    args = {k: v for k, v in body.model_dump().items() if v is not None}
    return _parse(await _invoke_tool(portfolio_update, args))


@app.delete("/api/portfolio/{record_id}", tags=["Portfolio"], summary="删除持仓记录")
async def portfolio_delete_endpoint(record_id: int):
    from xshare.tools.portfolio import portfolio_update
    return _parse(await _invoke_tool(portfolio_update, {"action": "delete", "id": record_id}))


# ---------------------------------------------------------------------------
# Sync Jobs
# ---------------------------------------------------------------------------

class SyncJobConfig(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = None


class SyncEnqueueBody(BaseModel):
    days: int | None = None
    pages: int | None = None
    retain_days: int | None = None
    backfill: bool | None = None
    limit: int | None = None
    force: bool | None = None
    years: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    overwrite: bool | None = None


class SyncCleanupBody(BaseModel):
    retain_days: int | None = None
    retain_count: int | None = None


@app.get("/api/sync/jobs", tags=["Sync"], summary="同步任务状态")
async def sync_jobs_endpoint():
    from xshare.tools.sync_job import sync_job
    return _parse(await _invoke_tool(sync_job, {"action": "status"}))


@app.get("/api/sync/coverage", tags=["Sync"], summary="数据覆盖度")
async def sync_coverage_endpoint(
    lookback_trading_days: int | None = Query(None, ge=1, le=500),
):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "coverage"}
    if lookback_trading_days is not None:
        args["lookback_trading_days"] = lookback_trading_days
    return _parse(await _invoke_tool(sync_job, args))


@app.get("/api/sync/watermarks", tags=["Sync"], summary="同步水位")
async def sync_watermarks_endpoint(
    dataset: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "watermarks", "limit": limit}
    if dataset:
        args["dataset"] = dataset
    return _parse(await _invoke_tool(sync_job, args))


@app.post("/api/sync/jobs/{job}/run", tags=["Sync"], summary="立即运行同步任务")
async def sync_job_run_endpoint(job: str, body: SyncEnqueueBody | None = None):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "run", "job": job}
    if body:
        args.update({k: v for k, v in body.model_dump().items() if v is not None})
    return _parse(await _invoke_tool(sync_job, args))


@app.post("/api/sync/jobs/{job}/enqueue", tags=["Sync"], summary="入队同步任务")
async def sync_job_enqueue_endpoint(job: str, body: SyncEnqueueBody | None = None):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "enqueue", "job": job}
    if body:
        args.update({k: v for k, v in body.model_dump().items() if v is not None})
    return _parse(await _invoke_tool(sync_job, args))


@app.post("/api/sync/jobs/all/enqueue", tags=["Sync"], summary="入队全部同步任务")
async def sync_job_enqueue_all_endpoint(body: SyncEnqueueBody | None = None):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "enqueue", "job": "all"}
    if body:
        args.update({k: v for k, v in body.model_dump().items() if v is not None})
    return _parse(await _invoke_tool(sync_job, args))


@app.get("/api/sync/history", tags=["Sync"], summary="同步历史")
async def sync_history_endpoint(
    job: str = Query("all"),
    limit: int = Query(20, ge=1, le=200),
):
    from xshare.tools.sync_job import sync_job
    return _parse(await _invoke_tool(sync_job, {"action": "history", "job": job, "limit": limit}))


@app.post("/api/sync/history/cleanup", tags=["Sync"], summary="清理同步历史")
async def sync_history_cleanup_endpoint(body: SyncCleanupBody | None = None):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "cleanup"}
    if body:
        if body.retain_days is not None:
            args["retain_days"] = body.retain_days
        if body.retain_count is not None:
            args["retain_count"] = body.retain_count
    return _parse(await _invoke_tool(sync_job, args))


@app.get("/api/sync/tasks/{task_id}", tags=["Sync"], summary="任务详情")
async def sync_task_detail_endpoint(task_id: int):
    from xshare.data.task_queue import get_task
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.post("/api/sync/tasks/{task_id}/cancel", tags=["Sync"], summary="取消任务")
async def sync_task_cancel_endpoint(task_id: int):
    from xshare.tools.sync_job import sync_job
    return _parse(await _invoke_tool(sync_job, {"action": "cancel", "task_id": task_id}))


@app.patch("/api/sync/jobs/{job}/config", tags=["Sync"], summary="更新任务配置")
async def sync_job_config_endpoint(job: str, body: SyncJobConfig):
    from xshare.tools.sync_job import sync_job
    args: dict = {"action": "config", "job": job}
    if body.enabled is not None:
        args["enabled"] = body.enabled
    if body.interval_minutes is not None:
        args["interval_minutes"] = body.interval_minutes
    if len(args) == 2:
        raise HTTPException(status_code=400, detail="需要至少提供 enabled 或 interval_minutes 之一")
    return _parse(await _invoke_tool(sync_job, args))



# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------

@app.post("/api/ai/chat", tags=["AI"], summary="AI 问股(SSE 流式)")
async def ai_chat_endpoint(request: Request):
    """AI 问股 — 流式返回 tool_call / tool_result / token / done 事件。"""
    body = await request.json()
    session_id = body.get("session_id", "default")
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    if not LLM_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="LLM API key 未配置(设置 XSHARE_LLM_API_KEY 或 OPENCODE_360ZHINAO_API_KEY)",
        )

    async def event_generator():
        try:
            async for sse_data in chat_stream(session_id, message):
                if await request.is_disconnected():
                    break
                yield sse_data  # {"data": json_str} dict
        except Exception as e:
            yield {"data": json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(event_generator())



@app.get("/api/ai/sessions", tags=["AI"], summary="AI 问股会话列表")
async def ai_sessions_endpoint():
    """列出所有 AI 问股会话。"""
    if not LLM_API_KEY:
        raise HTTPException(status_code=503, detail="LLM API key 未配置")
    from xshare.ai.agent import get_sessions

    return await get_sessions()


@app.get("/api/ai/sessions/{session_id}/history", tags=["AI"], summary="AI 问股会话历史")
async def ai_history_endpoint(session_id: str):
    """获取指定会话的完整消息历史。"""
    if not LLM_API_KEY:
        raise HTTPException(status_code=503, detail="LLM API key 未配置")
    from xshare.ai.agent import get_history

    return await get_history(session_id)




@app.delete("/api/ai/sessions/{session_id}", tags=["AI"], summary="删除 AI 问股会话")
async def ai_delete_endpoint(session_id: str):
    """删除指定会话的所有状态数据。"""
    if not LLM_API_KEY:
        raise HTTPException(status_code=503, detail="LLM API key 未配置")
    from xshare.ai.agent import delete_session

    ok = await delete_session(session_id)
    return {"deleted": ok}
# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["Health"], summary="健康检查")
async def health():
    return {"status": "ok"}
