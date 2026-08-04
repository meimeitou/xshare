# XShare — Copilot Instructions

XShare 是一个基于 [nanobot](https://github.com/nano-bot/nanobot) 的金融分析 MCP Server，通过工具调用提供股票/基金/行情数据分析能力。配套有 Next.js Web UI，通过 FastAPI 层调用相同的工具函数。

## 架构

- **`xshare/tools/`** — MCP 工具实现，每个文件导出一个 `async def tool_name(args: dict) -> str` 函数
- **`xshare/data/`** — 数据层：`provider.py`（基础/历史以 Tushare 为主；同日实时 AkShare-only，30s 超时，32 线程池）+ DuckDB 本地缓存（TTL 内联实现）
- **`xshare/indicators/`** — 技术指标计算（MA/MACD/RSI/KDJ/BOLL 等）
- **`xshare/web_server.py`** — FastAPI REST 服务，将所有工具函数包装为 HTTP 接口（`GET /api/health` 等）
- **`xshare/cli.py`** — CLI：`db init` / `portfolio import` / `serve` / `web`（同步用 Web `/sync`）
- **`frontend/`** — Next.js 16 Web UI，详见 [frontend/AGENTS.md](../frontend/AGENTS.md)
- **`workspace/`** — nanobot agent 运行时配置（`SOUL.md` 系统提示词、`skills/` 领域技能）。**不是 Python 包，不要混淆**
- **`data/xshare.duckdb`** — 本地 DuckDB 数据库

详见 [docs/architecture.md](../docs/architecture.md)。

## 构建 & 测试

```bash
uv sync                # 安装依赖
uv sync --dev          # 含测试工具

uv run xshare db init                     # 初始化数据库（首次必须）
uv run xshare serve                       # 启动 MCP Server（需 TUSHARE_TOKEN）
uv run xshare web                         # 启动 Web API（默认 localhost:8080）
uv run pytest                             # 运行测试（需先 uv sync --dev）

cd frontend && npm run dev                 # 启动前端（localhost:3000，需先 npm install）
```

## 添加新 MCP 工具（唯一注册入口）

1. 在 `xshare/tools/` 新建文件，实现 `async def your_tool(args: dict) -> str`，返回 `json.dumps(..., ensure_ascii=False)`
2. 在 `xshare/mcp_server.py` 顶部 `import` 该函数
3. 在 `TOOLS` dict 中添加 `"tool_name": (handler, json_schema)` 条目
4. （可选）在 `xshare/web_server.py` 中添加对应 FastAPI 端点

## 测试规范

- `tests/conftest.py` 提供 `db_conn(monkeypatch)` 和 `fake_provider()` fixture
- 注入方式：`monkeypatch.setattr(tool_module, "get_provider", lambda: fake_provider)`
- 所有 async 测试标注 `@pytest.mark.asyncio`
- 不使用 `pytest-mock`

## 关键规则

- **金融行情数据**：必须使用 MCP 工具，禁止 `web_search`/`web_fetch` 替代
- **实时新闻**：使用 `web_search`（nanobot 内置）或 `stock_news`（本地 DB）
- **返回格式**：所有工具返回 `json.dumps(..., ensure_ascii=False)`。错误时附 `"retry_same_args": false` 阻止 agent 重试循环
- **DuckDB 线程安全**：始终通过 `db.get_conn()` 获取连接（带 `_LockedConn` 代理），禁止直接 `duckdb.connect()`
- **同步任务**：`sync_job` 的 `action="run"` 同步执行；`action="enqueue"` 写入任务队列，需要 MCP Server worker 运行才会处理
- **环境变量**：`TUSHARE_TOKEN`（必须）、`WEB_SEARCH_PROVIDER`/`WEB_SEARCH_API_KEY`（可选）
- **数据源时序**：Tushare 日线通常在交易日 15:00-16:00 入库；`daily` 同步应在 15:30 之后执行，推荐 17:00 后
- **同日实时策略**：盘中同日实时行情只允许 AkShare（iTick 后续阶段接入）

## 常见陷阱

- `mineru-kie-sdk` 是 `doc_parse` 工具的文档解析依赖，不是标准金融库
- Provider 超时：所有上游调用经 `_provider_pool.submit(...).result(timeout=30)` 隔离，不会阻塞事件循环
- `workspace/` 是 nanobot 运行上下文，与 Python 包 `xshare/` 完全独立

---

## 通用编码规范

**原则：谨慎优于速度。**

1. **先思考再编码**：明确假设，有歧义时先问，不要默默选择
2. **简洁优先**：只写解决问题必须的代码，无投机性抽象，无额外 feature
3. **外科手术式修改**：只改被要求改的内容，匹配现有风格，不顺手重构
4. **目标驱动执行**：将任务转化为可验证目标（写测试 → 让测试通过），多步骤任务先列计划
