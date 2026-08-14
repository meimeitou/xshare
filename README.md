# XShare

股票智能分析助手，基于 [nanobot](https://github.com/nano-bot/nanobot) 框架，通过 MCP 协议提供金融数据分析工具。配套 Next.js Web UI 内置 **AI 问股** 对话助手。

## 功能

- **AI 问股**：自然语言对话，自动调用行情/指标/基本面工具，SSE 流式输出，支持多轮会话
- **个股分析**：实时行情、技术指标（MA/MACD/RSI/KDJ/BOLL/ATR）、基本面数据
- **条件筛选**：按 PE/PB/ROE/行业等多维度筛选股票
- **策略回测**：均线交叉、RSI 等策略的历史回测
- **大盘概览**：主要指数、涨跌统计、市场情绪
- **新闻检索**：个股相关新闻查询
- **文档数据支持**：支持从文档和图片中提取信息进行分析

## 快速开始

### 环境准备

```bash
# 安装依赖
uv sync

# 安装开发依赖
uv sync --dev
```

### 初始化数据库

```bash
uv run xshare db init

# 可选： 导入示例持仓数据
cp scripts/portfolio_template.csv scripts/portfolio.csv
# 修改 portfolio.csv 文件内容以符合你的持仓
# 先预览
uv run xshare portfolio import scripts/portfolio.csv --dry-run

# 确认无误后导入
uv run xshare portfolio import scripts/portfolio.csv
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少配置 TUSHARE_TOKEN
# AI 问股还需配置 XSHARE_LLM_API_KEY（及可选的 WEB_SEARCH_API_KEY）
```

### 启动 Web UI（推荐）

```bash
# 终端 1：FastAPI 后端 (localhost:8080)
uv run xshare web

# 终端 2：Next.js 前端 (localhost:3000)
cd frontend && npm install && npm run dev

# 或一键后台启动
make dev
```

打开 [http://localhost:3000/ask](http://localhost:3000/ask) 使用 AI 问股。

### 运行 MCP Server（独立测试）

```bash
export TUSHARE_TOKEN=YOUR_TOKEN
uv run xshare serve

# 也可沿用模块入口（nanobot 默认调用方式）
uv run python -m xshare.mcp_server

# MCP Inspector 调试
uv run mcp dev server.py
```

### 配合 nanobot 使用

当你希望将 XShare 与 nanobot 集成时，需要通过 nanobot-ai 工具使两者能够协同工作。

```bash
# install
pip install nanobot-ai
# or
uv tool install nanobot-ai

# upgrade
uv tool upgrade nanobot-ai
# or
pip install -U nanobot-ai
```

#### 配置运行 nanobot

1. 复制配置模板：`cp .env.example .env`，编辑 API Key 和 Token
2. 启动 nanobot：

   ```bash
   # cli 测试
   nanobot agent
   # 测试登录微信
   nanobot channels login weixin
   # gateway 测试
   nanobot gateway --workspace ./workspace
   ```

## 项目结构

```text
xshare/
├── workspace/              # nanobot workspace（Agent 人设 & Skills）
├── frontend/               # Next.js 16 Web UI（/ask 问股、/sync 同步等）
├── xshare/
│   ├── cli.py              # CLI 子命令（db/portfolio/serve/web）
│   ├── mcp_server.py       # MCP Server 入口（16 个工具）
│   ├── web_server.py       # FastAPI REST API + AI 问股 SSE
│   ├── ai/                 # Web AI 问股（LangGraph ReAct Agent）
│   ├── tools/              # MCP Tool 实现
│   ├── data/               # 数据层（DuckDB + 数据源适配）
│   └── indicators/         # 指标计算（纯 pandas）
├── config/                 # nanobot 配置示例
├── scripts/                # 辅助脚本（文档抓取、持仓 CSV 样例）
└── tests/
```

## 技术栈

- **Agent 框架**: nanobot（MCP）/ LangGraph（Web AI 问股）
- **工具协议**: MCP (Model Context Protocol)
- **Web 后端**: FastAPI + SSE
- **Web 前端**: Next.js 16 App Router
- **LLM**: LiteLLM（OpenAI-compatible，默认 GLM-5.2）
- **数据存储**: DuckDB（OLAP）+ SQLite（OLTP）
- **数据源**: AKShare + Tushare
- **指标计算**: pandas
