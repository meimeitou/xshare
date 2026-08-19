# 同步任务管理

XShare 通过 SQLite `sync_config` + `sync_task_queue` 管理同步任务；覆盖度水位写在 DuckDB `sync_watermark`。Web UI（`/sync`）、REST API、`sync_job` MCP 工具与 CLI 共用同一套队列。

## 设计原则

1. **历史数据本地优先**：日线 / 财务 / 基金净值 / 股票列表默认只读 DuckDB；缺数据返回明确错误，不在请求路径打外部 API（可用 `force_refresh`）。
2. **外部 API 只走 Sync Worker + 全局限速器**（`xshare/data/rate_limit.py`）。
3. **水位 + 日历调度**：日线类任务交易日 16:00 触发一次，并用 watermark 补洞。

## 任务类型

| 任务 | 调度 | 数据源 | 目标表 | 说明 |
|------|------|--------|--------|------|
| `news` | interval | 同花顺 7×24 | DuckDB `news` | 参数：`pages`、`retain_days` |
| `stock_basic` | interval（默认日） | Tushare | `stock_basic` | 需 `TUSHARE_TOKEN` |
| `index_basic` | interval（默认日） | Tushare | `index_basic` | 默认市场 SSE/SZSE/CSI（`XSHARE_INDEX_MARKETS`） |
| `etf_basic` | interval（默认日） | Tushare `etf_basic` | `etf_basic` | 默认上市状态 L |
| `trade_cal` | interval（默认周） | Tushare | `trade_cal` | 本地判定开市日 |
| `daily` | 交易日 16:00 | Tushare `pro.daily` | `stock_daily` | 补数可绕过窗口 |
| `index_daily` | 交易日 16:00 | Tushare `pro.index_daily` | `index_daily` | 按 `index_basic` 循环；补数可绕过窗口 |
| `fund_daily` | 交易日 16:00 | Tushare `pro.fund_daily` | `fund_daily` | 按交易日批量；失败回退按 `etf_basic` |
| `daily_basic` | 交易日 16:00 | Tushare | `stock_daily_basic` | 全市场 PE/PB |
| `finance` | interval（默认周） | Tushare | `stock_finance` | 分片 + 水位断点 |
| `fund_nav` | 交易日 16:00 | Tushare | `fund_nav` | 关注列表 / fund_basic |
| `quote` | interval（默认 5 分钟，仅交易时段 09:25-11:35 / 12:55-15:10 入队） | AkShare 新浪 | `quote_snapshot` / `index_snapshot` / `sector_snapshot` | 实时行情快照；无需 `TUSHARE_TOKEN`；保留 `XSHARE_QUOTE_RETAIN_DAYS` 天 |

## 启动自检

服务启动（`XSHARE_AUTO_SYNC!=0`）时：

1. 入队 `trade_cal`、`stock_basic`、`index_basic`、`etf_basic`
2. 处于 16:00 窗口且对应日线表非空时，入队当日增量 `daily` / `index_daily` / `fund_daily` / `daily_basic`（`days=1`）
3. **历史数据补全不再自动触发**：库空时日线类不入队 backfill，应由前端"一次性补全"接口（`start_date`/`end_date` + `overwrite`）显式触发
4. 入队 `finance`；窗口内入队 `fund_nav`
5. 入队 `news`
6. 按 `XSHARE_SYNC_HISTORY_RETAIN_DAYS` 清理旧任务日志

## REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sync/jobs` | 任务配置 + 队列 + 水位摘要 + 限速统计 |
| GET | `/api/sync/coverage` | 日线覆盖率 |
| GET | `/api/sync/watermarks?dataset=&limit=` | 水位明细 |
| POST | `/api/sync/jobs/{job}/enqueue` | 入队，body 可选 `days/pages/retain_days/backfill/limit/force/years/start_date/end_date/overwrite` |
| POST | `/api/sync/jobs/all/enqueue` | 全部入队 |
| GET | `/api/sync/history?job=&limit=` | 历史记录 |
| POST | `/api/sync/history/cleanup` | 清理日志 |
| GET | `/api/sync/tasks/{id}` | 任务详情 |
| POST | `/api/sync/tasks/{id}/cancel` | 取消 queued 任务 |
| PATCH | `/api/sync/jobs/{job}/config` | 启停 / 改间隔 |

## MCP `sync_job` 动作

`status` | `watermarks` | `config` | `enqueue` / `run` | `history` | `coverage` | `cancel` | `cleanup`

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `XSHARE_DAILY_MIN_TRADING_DAYS` | 252 | 启动自检目标交易日 |
| `XSHARE_DAILY_BACKFILL_DAYS` | 252 | 单次补数回溯交易日 |
| `XSHARE_DAILY_SYNC_QPS` / `XSHARE_TUSHARE_MIN_INTERVAL` | 4 QPS / 0.25s | Tushare 全局限速（低于官方 500/min） |
| `XSHARE_TUSHARE_RATE_RETRIES` / `XSHARE_TUSHARE_RATE_COOLDOWN` | 5 / 65s | 遇「频率超限」冷却后重试 |
| `XSHARE_SYNC_HISTORY_RETAIN_DAYS` | 30 | 任务日志保留天数 |
| `XSHARE_FINANCE_SYNC_LIMIT` | 200 | 单次财务同步股票数 |
| `XSHARE_FUND_NAV_SYNC_LIMIT` | 50 | 单次基金净值同步数 |

## 操作入口

同步全部通过 Web 前端 `/sync` 页面操作；REST API 与 MCP `sync_job` 共用同一队列。无 CLI sync 子命令。

## 队列语义

- 单 worker 串行执行；`priority` 越小越优先
- 状态：`queued` → `running` → `success` | `skipped` | `error` | `cancelled`
- `error` 指数退避重试（最多 3 次）
- `running` 超过 5 分钟无心跳视为僵尸，重新入队
