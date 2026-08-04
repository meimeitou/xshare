# XShare

股票智能分析助手，基于 [nanobot](https://github.com/nano-bot/nanobot) 框架，通过 MCP 协议提供金融数据分析工具。

## 功能

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

### 运行 MCP Server（独立测试）

```bash
# 或通过环境变量
export TUSHARE_TOKEN=YOUR_TOKEN
uv run xshare serve

# 也可沿用模块入口（nanobot 默认调用方式）
uv run python -m xshare.mcp_server

# 也可通过 MCP Server 入口运行（nanobot 默认调用方式）
uv run mcp dev server.py
```

### 配合 nanobot 使用

当你希望将 XShare 与 nanobot 集成时，需要通过 nanobot-ai 工具使两者能够协同工作。

为了使 XShare 能够与 nanobot 集成，需要先安装 nanobot-ai 工具。

```bash
# install
pip install nanobot-ai
# or
uv tool install nanobot-ai

# upgrade
uv tool upgrade nanobot-ai
# or
pip install -U nanobot-ai

## nanobot 配置文件生成
# nanobot onboard --config config/config.json
```

#### 配置运行 nanobot

1. 复制配置模板：

   ```bash
   cp .env.example .env
   ```

2. 修改配置中的 API Key 和 Token

   打开 `.env`, 编辑相应字段

3. 启动 nanobot：

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
├── xshare/
│   ├── cli.py               # CLI 子命令（db/portfolio/sync/serve）
│   ├── mcp_server.py       # MCP Server 入口
│   ├── tools/              # MCP Tool 实现（10 个工具）
│   ├── data/               # 数据层（DuckDB + 数据源适配）
│   └── indicators/         # 指标计算（纯 pandas）
├── config/                 # nanobot 配置示例
├── scripts/                # 辅助脚本（文档抓取、持仓 CSV 样例）
└── tests/
```

## 技术栈

- **Agent 框架**: nanobot
- **工具协议**: MCP (Model Context Protocol)
- **数据存储**: DuckDB
- **数据源**: AKShare + Tushare
- **指标计算**: pandas
