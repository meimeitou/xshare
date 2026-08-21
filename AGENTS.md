# XShare — LLM Agent Instructions

XShare 是一个金融分析 MCP Server，为 nanobot AI Agent 提供股票/基金/行情数据分析能力。配套 Next.js 16 Web UI 内置 **AI 问股**（LangGraph ReAct Agent），通过 FastAPI 层调用相同的工具函数。

## 架构概览

```
nanobot agent ←→ MCP Server (xshare/mcp_server.py)
                        ↓
                   xshare/tools/      ← 每个文件导出 async def tool(args: dict) -> str
                        ↓
                   xshare/data/provider.py  ← 多源 failover: Tushare/AkShare + DuckDB 缓存
                        ↓
                   data/xshare.duckdb  ← 本地数据库

Web UI /ask → FastAPI /api/ai/* → xshare/ai/agent.py (LangGraph) → xshare/ai/tools.py → xshare/tools/
Web UI 其他页 → FastAPI (xshare/web_server.py) → xshare/tools/
```

### 目录结构

| 路径 | 用途 |
|------|------|
| `xshare/tools/` | MCP 工具实现（16 个工具），每个文件一个 `async def tool_name(args: dict) -> str` |
| `xshare/ai/` | Web AI 问股：`agent.py`（LangGraph ReAct + SSE 流式）、`tools.py`（MCP 工具 → LangChain StructuredTool 适配） |
| `xshare/data/` | 数据层：`provider.py`（多源 failover + DuckDB 缓存 + TTL）、`db.py`（DuckDB OLAP）、`sqlite_db.py`（SQLite OLTP）、`sync_config.py`（定时同步）、`task_queue.py`（任务队列） |
| `xshare/data/sources/` | 数据源实现：`akshare_provider.py`、`tushare_provider.py`、`ths_news.py` |
| `xshare/indicators/` | 技术指标（technical.py）+ 基本面指标（fundamental.py），纯 pandas 实现 |
| `xshare/cli.py` | CLI：`db init` / `portfolio import` / `serve` / `web`（同步请用 Web `/sync`） |
| `xshare/mcp_server.py` | MCP Server 主入口。`TOOLS` dict 是唯一工具注册表 |
| `xshare/web_server.py` | FastAPI REST 包装层（含 `/api/ai/*` SSE 问股端点），CORS 默认允许 localhost |
| `server.py` | FastMCP 包装器，仅用于 `mcp dev server.py` 本地调试 |
| `frontend/` | Next.js 16 App Router，详见 `frontend/AGENTS.md` |
| `workspace/` | nanobot 运行时上下文（SOUL.md 人设、USER.md 偏好、skills/ 领域技能）。**不是 Python 包** |
| `tests/` | pytest + pytest-asyncio，所有 async 测试标 `@pytest.mark.asyncio` |
| `data/xshare.duckdb` | 本地 DuckDB 数据库，DEFAULT_DB_PATH 可通过 `XSHARE_DB_PATH` 环境变量覆盖 |

### 数据流向

1. **Agent 调用工具** → MCP Server 解析参数 → 调用 `xshare/tools/` 中对应函数
2. **工具函数** → 通过 `get_provider()` 获取 `ProviderManager` → 按优先级 failover 调用数据源
3. **数据源** → 当天实时仅 AkShare；历史读 DuckDB；sync 用 Tushare 写库
4. **缓存层** → DuckDB 内联 TTL（股票列表 7 天、日线 1 天、财务数据 1 天）
5. **返回** → `json.dumps(result, ensure_ascii=False)` → MCP → Agent

### Web AI 问股数据流

1. **前端** `/ask` 页 → `POST /api/ai/chat`（SSE）或 `GET/DELETE /api/ai/sessions/*`
2. **Agent** `xshare/ai/agent.py` — LangGraph `create_react_agent` + `InMemorySaver` 多轮会话
3. **工具** `xshare/ai/tools.py` — 白名单 11 个行情工具 + `web_search`（Tavily），复用 `web_server._invoke_tool` 线程池隔离
4. **LLM** `ChatLiteLLM`（OpenAI-compatible），默认 360 智脑 GLM-5.2
5. **SSE 事件** `tool_call` / `tool_result` / `token` / `done` / `error`

### 数据源策略

| 场景 | 数据源 | 说明 |
|------|--------|------|
| 当天实时行情 / 大盘快照 | quote_snapshot 缓存（quote 任务，新浪，交易时段 5 分钟） | 缓存优先；miss 回退 AkShare 新浪实时；个股再回退 `stock_daily` |
| 北向资金 | Tushare `moneyflow_hsgt` | 日终数据（沪股通/深股通净流入），需 `TUSHARE_TOKEN`；东财接口已停用 |
| 东财接口（`*_em` 等） | 禁用 | 易封禁；akshare 历史/净值走 Tushare failover |
| 日线同步 | Tushare | 仅交易日 16:00 后执行；水位补洞 |
| 历史数据读取 | DuckDB | local-first，默认不打外部 API |
| 新闻 | 同花顺 7×24 → DuckDB | 默认保留 1 天 |
| 图片/文档解析 | MinerU (doc_parse) | 通过 `mineru-kie-sdk` |

## 常用命令

```bash
uv sync                      # 安装 Python 依赖
uv sync --dev                # 含测试工具
uv run xshare db init        # 初始化 DuckDB（首次必须）
uv run xshare serve          # MCP Server (stdio transport，需 TUSHARE_TOKEN)
uv run xshare web            # FastAPI REST API (localhost:8080)
# 同步任务：打开前端 /sync 页面（勿用 CLI）
uv run pytest                # 运行测试（需先 uv sync --dev）
uv run mcp dev server.py     # MCP Inspector 调试模式

cd frontend && npm run dev   # Next.js (localhost:3000)，AI 问股需配置 XSHARE_LLM_API_KEY

make dev                     # 后台启动 API + 前端（日志写入 .logs/）
make kill                    # 停止所有后台进程
make logs                    # 查看日志
```

## 添加新 MCP 工具

**唯一注册入口** — 三步必须全部完成：

1. 在 `xshare/tools/` 新建文件，实现 `async def tool_name(args: dict) -> str`
   - 返回 `json.dumps(result, ensure_ascii=False)`
   - 错误时附 `"retry_same_args": false` 阻止 agent 无限重试
2. 在 `xshare/mcp_server.py` 顶部 import，在 `TOOLS` dict 中添加 `"tool_name": (handler, json_schema)`
3. （可选）在 `xshare/web_server.py` 中添加对应 FastAPI 路由
4. （可选）若需 Web AI 问股可用，在 `xshare/ai/tools.py` 的 `AI_TOOL_NAMES` 白名单中添加工具名

## AI 问股

- **入口**：前端 `/ask`；后端 `xshare/ai/agent.py`
- **工具白名单**：`AI_TOOL_NAMES`（11 个行情/市场工具，不含 `portfolio_*` / `sync_job` / `backtest_run` / `doc_parse`）+ `web_search`
- **会话存储**：`InMemorySaver`（进程内，重启丢失）；`session_id` 由前端生成 UUID
- **配置**：`XSHARE_LLM_API_BASE` / `XSHARE_LLM_API_KEY`（或 `OPENCODE_360ZHINAO_API_KEY`）/ `XSHARE_LLM_MODEL`；搜索需 `WEB_SEARCH_API_KEY`（Tavily）
- **规则**：金融数值必须来自 MCP 工具；`web_search` 仅作情报补充，不可替代行情工具

## 关键规则

- **金融数据来源**：必须使用 MCP 工具。禁止用 `web_search`/`web_fetch` 获取行情、指数、财务数据
- **`web_fetch` 限制**：仅当用户消息中已明确包含完整 URL 时才调用。禁止自行构造/推断 URL
- **新闻**：个股新闻用 `stock_news`（本地 DB），实时资讯搜索用 `web_search`（nanobot 内置）
- **数据源时序**：Tushare 日线交易日 15:00-16:00 入库，同步应在 16:00 后
- **DuckDB 连接**：始终通过 `xshare.data.db.get_conn()` 获取（per-call 连接，用完即弃）。禁止直接 `duckdb.connect()`
- **SQLite OLTP**：`sync_config` / `sync_task_queue` / `portfolio` 在 `xshare.data.sqlite_db`。共享连接经 RLock 串行化；多语句事务用 `sqlite_critical()`。时间戳统一 UTC
- **Provider 超时**：所有上游调用经 `ThreadPoolExecutor(32).submit().result(timeout=30)` 隔离，不阻塞事件循环
- **同步入口**：Web `/sync` 页面（或 MCP `sync_job`）；服务启动后由后台 timer/worker 调度，无 CLI sync 子命令
- **workspace/** 是 nanobot 运行上下文，不是 Python 包，不要作为代码模块导入

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `TUSHARE_TOKEN` | 生产用 | Tushare Pro API token |
| `XSHARE_LLM_API_BASE` | Web AI | LLM API base URL（默认 `https://api.360.cn/v1`，须含 `/v1`） |
| `XSHARE_LLM_API_KEY` | Web AI | LLM API key；也可用 `OPENCODE_360ZHINAO_API_KEY` |
| `XSHARE_LLM_MODEL` | Web AI | 模型名（默认 `z-ai/glm-5.2`） |
| `WEB_SEARCH_PROVIDER` | 可选 | nanobot 搜索后端 (tavily/brave/等) |
| `WEB_SEARCH_API_KEY` | 可选 | Tavily API key（Web AI `web_search` 工具 + nanobot 搜索） |
| `XSHARE_DB_PATH` | 可选 | DuckDB 路径（默认 `data/xshare.duckdb`） |
| `XSHARE_SQLITE_PATH` | 可选 | SQLite OLTP 路径（默认 `data/xshare.sqlite`） |
| `XSHARE_PROVIDER_TIMEOUT` | 可选 | Provider 调用超时秒数（默认 30） |
| `XSHARE_TOOL_TIMEOUT` | 可选 | 单次工具调用硬超时秒数（默认 120） |
| `XSHARE_AUTO_SYNC` | 可选 | MCP 启动时自动同步（默认 1=开启） |
| `XSHARE_NEWS_SYNC_INTERVAL` | 可选 | 新闻同步间隔分钟（默认 15） |
| `XSHARE_QUOTE_SYNC_INTERVAL` | 可选 | 行情快照同步间隔分钟（默认 5，仅交易时段入队） |
| `XSHARE_QUOTE_RETAIN_DAYS` | 可选 | 行情快照保留天数（默认 5） |
| `XSHARE_DAILY_SYNC_INTERVAL` | 可选 | 日线展示兜底间隔（日历任务实际按 16:00 触发） |
| `XSHARE_TUSHARE_MIN_INTERVAL` | 可选 | Tushare 全局限速最小间隔秒（默认 0.25，约 240/min，低于 500/min 上限） |
| `XSHARE_TUSHARE_RATE_RETRIES` | 可选 | 频率超限时重试次数（默认 5） |
| `XSHARE_TUSHARE_RATE_COOLDOWN` | 可选 | 频率超限冷却秒数（默认 65） |
| `XSHARE_DAILY_MIN_TRADING_DAYS` | 可选 | 启动自检目标交易日（默认 252） |
| `XSHARE_DAILY_BACKFILL_DAYS` | 可选 | 单次日线补数回溯交易日（默认 252） |
| `XSHARE_SYNC_HISTORY_RETAIN_DAYS` | 可选 | 任务队列日志保留天数（默认 30，0=关闭启动清理） |
| `XSHARE_SYNC_WORKERS` | 可选 | 任务队列并发 worker 数量（默认 5） |
| `MINERU_PIPELINE_ID` | 可选 | MinerU 文档解析 pipeline |
| `XSHARE_CORS_ALLOW_ORIGINS` | 可选 | FastAPI CORS 白名单（逗号分隔） |
| `TUSHARE_PRIORITY`/`AKSHARE_PRIORITY` | 可选 | Provider 优先级调整 |

## 测试规范

- `tests/conftest.py` 提供 `db_conn`（内存 DuckDB + 内存 SQLite）+ `sqlite_conn` + `fake_provider()` fixture
- 测试中注入 Provider: `monkeypatch.setattr(tool_module, "get_provider", lambda: fake_provider)`
- 所有 async 测试用 `@pytest.mark.asyncio`
- 不使用 `pytest-mock`，只用 built-in `monkeypatch`
- `FakeProvider` 在 conftest 中可扩展方法（继承覆盖），`FailingProvider` 用于测试容错分支
- `make_daily_history()` / `make_financial_data()` 是数据构造辅助函数

## DuckDB 表结构（OLAP）

| 表 | 主键 | 用途 |
|----|------|------|
| `stock_basic` | `code` | 股票基本信息（名称/市场/行业/上市日期） |
| `index_basic` | `code` | 指数基本信息（名称/市场/发布方/类别） |
| `etf_basic` | `code` | ETF 基本信息（跟踪指数/管理人/交易所） |
| `stock_daily` | `(code, trade_date)` | 日线 OHLCV |
| `index_daily` | `(code, trade_date)` | 指数日线 OHLCV + pct_chg |
| `fund_daily` | `(code, trade_date)` | ETF 日线 OHLCV + pct_chg |
| `stock_daily_basic` | `(code, trade_date)` | 每日 PE/PB 等 |
| `stock_finance` | `(code, end_date)` | 季度财务数据 |
| `trade_cal` | `cal_date` | 交易日历 |
| `sync_watermark` | `(dataset, key)` | 数据集同步水位 |
| `news` | `id` (URL hash) | 新闻（按 publish_time 清理，保留可配天数） |
| `fund_basic` | `code` | 基金基本信息 |
| `quote_snapshot` | `(code, ts)` | A 股个股实时快照（quote 任务，新浪源） |
| `index_snapshot` | `(code, ts)` | 指数实时快照 |
| `sector_snapshot` | `(name, ts)` | 行业板块快照（自带领涨股） |

## SQLite 表结构（OLTP）

| 表 | 主键 | 用途 |
|----|------|------|
| `portfolio` | 自增 `id` | 用户持仓交易记录 |
| `sync_config` | `job` | 同步任务配置与运行状态 |
| `sync_task_queue` | 自增 `id` | 异步任务队列 |

## 技术指标

纯 pandas 实现（无 ta-lib C 依赖），支持：MA, EMA, MACD, RSI, KDJ, BOLL, ATR, VOL_MA, OBV, VWAP, DMI, NINE_TURN, TREND。通过 `stock_indicators` 工具按需计算。

## 同步系统

1. **sync_config** — SQLite 配置；日历任务（daily/index_daily/fund_daily/daily_basic）交易日 16:00 触发；其余 interval
2. **task_queue** — 异步队列 + 退避重试 + lease 僵尸回收
3. **watermark / rate_limit** — DuckDB 水位 + 按源全局限速
4. **sync_job** — MCP：`status`/`watermarks`/`config`/`enqueue`/`history`/`coverage`/`cancel`/`cleanup`
5. **sync_runtime** — MCP 与 Web 共用 worker + 定时 loop

任务类型：`news`、`stock_basic`、`index_basic`、`etf_basic`、`trade_cal`、`daily`、`index_daily`、`fund_daily`、`daily_basic`、`finance`、`quote`（交易时段 5 分钟行情快照）。详见 `docs/sync-management.md`。

## 常见陷阱

- `mineru-kie-sdk` 是 `doc_parse` 的文档解析依赖，不是标准金融库
- Provider 超时不抛常规异常，而是继续下一个 provider（failover）
- `get_provider()` 返回的是 `ProviderManager` 实例（门面模式），不是单个 DataProvider
- `workspace/` 下所有 `skill.md` 文件中引用工具时，必须使用 `mcp__xshare__<tool_name>` 格式（MCP 命名空间前缀）
- Web AI 问股与 nanobot 共用 `xshare/tools/` 实现，但走 `xshare/ai/tools.py` 适配层，不经过 MCP transport
- AI 问股会话存在内存中，多 worker / 重启后会话不共享
- FastAPI `web_server.py` 与 MCP 共用 `sync_runtime.spawn_sync_runtime()`（worker + 定时 loop）
- 队列任务结果：`ok`→success，`skipped`→skipped（不重试），`error`→指数退避重试；禁止把 error/skipped 记成 success
- `xshare db init` 会 DROP DuckDB 里旧的 OLTP 表（portfolio/sync_*），数据不会自动迁到 SQLite；持仓需重新导入
- SQLite OLTP 时间戳必须用 UTC（`now_ts()` / `datetime.utcnow` 坐标系），禁止与本地 `datetime.now()` 混用
