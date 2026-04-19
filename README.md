# XShare

股票/基金智能分析助手，基于 [nanobot](https://github.com/nano-bot/nanobot) 框架，通过 MCP 协议提供金融数据分析工具。

## 功能

- **个股分析**：实时行情、技术指标（MA/MACD/RSI/KDJ/BOLL/ATR）、基本面数据
- **基金分析**：基金信息查询、绩效分析（年化收益/最大回撤/夏普比率）
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
uv run python scripts/init_db.py

# 可选： 导入示例持仓数据
cp scripts/portfolio_template.csv scripts/portfolio.csv
# 修改 portfolio.csv 文件内容以符合你的持仓
# 先预览
uv run python scripts/import_portfolio.py scripts/portfolio.csv --dry-run

# 确认无误后导入
uv run python scripts/import_portfolio.py scripts/portfolio.csv
```

### 运行 MCP Server（独立测试）

```bash
# 通过命令行参数传入 Tushare token
uv run python -m xshare.mcp_server --tushare-token YOUR_TOKEN

# 或通过环境变量
export TUSHARE_TOKEN=YOUR_TOKEN
uv run python -m xshare.mcp_server
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
```

#### 测试llm function call能力(可选)

```bash
uv run python scripts/test_parallel_tools.py --api-base https://api.example.com/v1 --api-key sk-xxx --model gpt-4
```

#### 配置运行 nanobot

1. 复制配置模板：

   ```bash
   cp config/nanobot.example.json ~/.nanobot/config.json
   ```

2. 修改配置中的 API Key 和 Token

   打开 `~/.nanobot/config.json`,编辑相应字段，或者使用环境变量快速配置相应的值。

3. 启动 nanobot：

   ```bash
   export TUSHARE_TOKEN=your_tushare_token
   export DEFAULT_API_KEY=your_custom_api_key
   export DEFAULT_API_BASE=your_custom_api_base
   export DEFAULT_MODEL=glm-5
   export DEFAULT_PROVIDER=custom
   # cli 测试
   nanobot agent
   # 测试登录微信
   nanobot channels login weixin
   # gateway 测试
   nanobot gateway
   ```

## 项目结构

```text
xshare/
├── workspace/              # nanobot workspace（Agent 人设 & Skills）
├── src/xshare/
│   ├── mcp_server.py       # MCP Server 入口
│   ├── tools/              # MCP Tool 实现（10 个工具）
│   ├── data/               # 数据层（DuckDB + 数据源适配）
│   └── indicators/         # 指标计算（纯 pandas）
├── config/                 # nanobot 配置示例
├── scripts/                # 脚本（数据库初始化等）
└── tests/
```

## 技术栈

- **Agent 框架**: nanobot
- **工具协议**: MCP (Model Context Protocol)
- **数据存储**: DuckDB
- **数据源**: AKShare + Tushare
- **指标计算**: pandas
