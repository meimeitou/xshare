# 新闻定时同步

XShare 支持自动同步同花顺 7×24 实时新闻到本地 DuckDB，数据库默认只保留 1 天新闻。

## 数据源

同花顺实时新闻 <https://news.10jqka.com.cn/realtimenews.html>

## 方式一：nanobot 内置定时任务（推荐）

启动 nanobot 后，在对话中直接告诉 Agent：

```
帮我创建一个定时任务，每 10 分钟同步一次同花顺新闻
```

Agent 会自动调用 `createScheduledTask` 创建 cron 任务，并在每次触发时调用 `sync_news` 工具。

### 管理任务

| 操作 | 对话示例 |
|------|---------|
| 查看任务列表 | "列出所有定时任务" |
| 暂停任务 | "暂停新闻同步任务" |
| 恢复任务 | "恢复新闻同步任务" |
| 立即执行一次 | "立即同步一次新闻" |
| 删除任务 | "删除新闻同步任务" |

任务执行记录可在 nanobot UI（<http://localhost:8080）中查看。>

## 方式二：命令行脚本

### 单次同步

```bash
uv run python scripts/sync_news.py
```

### 定时同步（每 10 分钟）

```bash
uv run python scripts/sync_news.py --interval 10
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--pages` | 5 | 每次抓取页数 |
| `--interval` | 0 | 同步间隔（分钟），0 为只执行一次 |
| `--retain-days` | 1 | 新闻保留天数 |

### 后台运行

```bash
nohup uv run python scripts/sync_news.py --interval 10 > data/sync_news.log 2>&1 &
```

### cron 方式

```bash
# crontab -e
*/10 * * * * cd ~/workspace/xshare && uv run python scripts/sync_news.py --pages 3 >> data/sync_news.log 2>&1
```

## MCP 工具

`sync_news` 已注册为 MCP 工具，可在对话中直接调用：

```
同步一下最新新闻
```

参数：

- `pages`：抓取页数（1-10，默认 3）
- `retain_days`：保留天数（默认 1）

## 查询新闻

同步完成后，通过 `stock_news` 工具查询：

```
查一下最近关于半导体的新闻
比亚迪最新新闻
```
