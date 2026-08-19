# 日线行情同步

XShare 将全市场 A 股日线行情（OHLCV）同步到本地 DuckDB 的 `stock_daily` 表，并写入 `sync_watermark`（按交易日）。工具侧 **本地优先**：`get_daily_history` 默认不打外部 API。

## 数据源

- 基础/历史日线同步：Tushare Pro `pro.daily(trade_date=)`，单次请求拉取全市场单日数据。
- 实时行情 / 大盘快照：仅 AkShare；个股失败回退本地日线缓存；不回退 Tushare。
- 历史日线读取：DuckDB（由 sync 写入）。

> 重要：Tushare 日线数据通常在交易日 15:00-16:00 入库，**定时任务在交易日 16:00 后触发**。

## 方式一：MCP / Web 自动同步（推荐）

服务启动后，`daily` 按日历在交易日 16:00 入队一次当日增量；并扫描 watermark 缺口自动补洞（最近 30 个交易日内的非 ok 日期）。通过 `sync_job` 或 Web `/sync` 管理：

| 操作 | 对话示例 |
|------|---------|
| 查看状态（含水位） | "日线同步状态怎么样" |
| 暂停任务 | "暂停日线同步" |
| 立即执行 | "现在同步一次日线" |
| 一次性补全时间段 | enqueue `daily` 带 `start_date`/`end_date` + `overwrite=true` |

## 操作入口

同步全部通过 Web 前端 `/sync` 页面操作（运行 / 一次性补全 / 启停 / 历史）；API 与 MCP `sync_job` 共用同一队列。

历史数据补全由前端"一次性补全"区块触发：填写起止日期（YYYY-MM-DD）并勾选"覆盖已有"后，点击对应覆盖率卡片的"补全"按钮，即按区间拉取并覆盖。不填日期则补最近一年缺口（`days=252, backfill=true`）。库空时启动不会自动跑 backfill，需手动触发。

## 下游消费者

同步完成后，以下工具直接从 `stock_daily` 读取：

- `trend_scanner`：趋势扫描
- `backtest_run`：策略回测
- `stock_indicators`：技术指标（响应含 `source` / `as_of` / `is_stale`）
