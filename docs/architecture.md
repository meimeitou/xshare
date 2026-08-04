# XShare — 股票/基金智能分析助手 MVP 框架文档

> 基于 [nanobot](https://github.com/HKUDS/nanobot) · 个人开发 / 小团队定位 · Agent Loop + Skills · 受控工具调用 · 规则与模型混合

---

## 1. 整体定位

| 维度 | 说明 |
|------|------|
| **基座框架** | nanobot — 超轻量个人 AI Agent，提供 Agent Loop、Channel、Session、Memory、Tool 执行等基础设施 |
| **LLM 负责** | 理解用户意图、决定调用哪些工具、组织自然语言解释 |
| **传统代码负责** | 指标计算、回测引擎、条件筛选、数据校验、风控规则（以 nanobot Tool 形式注册） |
| **交互入口** | nanobot Gateway — 微信(weixin) / 企业微信(wecom) / Telegram / CLI / API 等多通道统一接入 |

核心原则：**LLM 不碰数字**——所有数值结论由确定性代码（Tool）产出，LLM 只做 "理解→调度→表达"。

## 1.2 数据源策略（2026-07）

| 数据类型 | 本阶段数据源策略 | 说明 |
|------|------|------|
| 当天实时行情 / 大盘快照 | 仅 AkShare | 个股失败回退本地 `stock_daily`（`is_delayed`）；大盘失败返回字段级 error；**不回退 Tushare** |
| 历史数据读取 | DuckDB（sync 写入） | 日线/财务/列表等读路径默认不打外部 API |
| 基础/历史同步 | Tushare | sync 任务写库（当前无实时权限） |
| Tushare 日线可用时间 | 交易日 15:00-16:00 入库 | 运营建议 17:00 后在 `/sync` 触发或依赖日历任务 |

实现约束：

- 实时类接口（行情、指数、涨跌统计、板块、成交额、北向、涨跌幅榜）**仅 AkShare**，不因盘中/盘后切换，也不回退 Tushare。
- AkShare 失败时：个股回退本地 `stock_daily`；大盘由工具层返回 `*_error`。
- `daily` / `daily_basic` / `fund_nav` 定时任务仅在交易日 **17:00** 后执行。
- 历史类数据（日线/财务/基金/股票列表）默认 **local-first**：读路径不打外部 API。

### 1.1 为什么选择 nanobot

| 自研 | nanobot |
|------|---------|
| 需自建 Agent Loop、Channel 适配、Session 管理、Memory 层 | **全部开箱即用** |
| 需自写 微信接入 (WeChatFerry 等) | 内置 weixin channel，`nanobot channels login weixin` 扫码即用 |
| 需自建 工具注册 & schema 校验 | Agent Loop 原生支持 LLM function calling + tool execution |
| 需自建 上下文压缩 | 内置 Consolidator + Auto Compact |
| 需自建 长期记忆 | 内置 Dream 分层记忆 (SOUL.md / USER.md / MEMORY.md) |
| 需自建 多 LLM Provider 切换 | 内置 20+ Provider (OpenRouter / DeepSeek / Ollama / ...) |

**XShare 只需专注于：金融领域的 Skills + Tools + 数据层。**

---

## 2. 系统架构总览

```
┌───────────────────────────────────────────────────────┐
│                     nanobot 基座                       │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Channels (消息通道)                              │  │
│  │  微信(weixin) · 企业微信(wecom) · Telegram · CLI  │  │
│  │  · Discord · Feishu · API · ...                  │  │
│  └──────────────────┬──────────────────────────────┘  │
│                     │                                 │
│  ┌──────────────────▼──────────────────────────────┐  │
│  │  Bus (消息总线)                                   │  │
│  │  → 消息路由、会话隔离、进度推送                      │  │
│  └──────────────────┬──────────────────────────────┘  │
│                     │                                 │
│  ┌──────────────────▼──────────────────────────────┐  │
│  │  Agent Loop (核心循环)                            │  │
│  │  ┌───────────────────────────────────────────┐  │  │
│  │  │  System Prompt + Skills + Context          │  │  │
│  │  │       ↓                                    │  │  │
│  │  │  LLM ←→ Tool Execution (循环)              │  │  │
│  │  │       ↓                                    │  │  │
│  │  │  Response → Channel                        │  │  │
│  │  └───────────────────────────────────────────┘  │  │
│  └──────────────────┬──────────────────────────────┘  │
│                     │                                 │
│  ┌──────────────────▼──────────────────────────────┐  │
│  │  Built-in Tools                                  │  │
│  │  web_search · web_fetch · exec · read_file · ... │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  ┌─────────────────┐  ┌────────────────────────────┐  │
│  │  Session 管理    │  │  Memory (Dream)             │  │
│  │  会话持久化       │  │  history.jsonl → MEMORY.md  │  │
│  │  Auto Compact   │  │  SOUL.md · USER.md          │  │
│  └─────────────────┘  └────────────────────────────┘  │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Providers (LLM 多模型)                           │  │
│  │  OpenRouter · DeepSeek · Ollama · OpenAI · ...   │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Cron / Heartbeat (定时任务)                      │  │
│  └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
                          │
          ┌───────────────▼───────────────┐
          │    XShare 领域层 (我们写的部分)   │
          │                               │
          │  ┌─────────────────────────┐  │
          │  │  Skills (技能)            │  │
          │  │  stock · fund · market   │  │
          │  └────────────┬────────────┘  │
          │               │               │
          │  ┌────────────▼────────────┐  │
          │  │  Tools (工具)            │  │
          │  │  行情查询 · 指标计算      │  │
          │  │  回测引擎 · 条件筛选      │  │
          │  │  基金分析 · 风控校验      │  │
          │  └────────────┬────────────┘  │
          │               │               │
          │  ┌────────────▼────────────┐  │
          │  │  Data Layer (数据层)     │  │
          │  │  AKShare · Tushare      │  │
          │  │  DuckDB 本地缓存         │  │
          │  └─────────────────────────┘  │
          └───────────────────────────────┘
```

---

## 3. nanobot 核心概念映射

### 3.1 Agent Loop（替代自研 Workflow Engine）

nanobot 的核心不是 DAG 工作流，而是一个 **Agent Loop**：

```
LLM 收到 (system_prompt + skills + 用户消息 + 上下文)
  → LLM 决定调用哪些 Tool（function calling）
  → nanobot 执行 Tool，返回结果
  → LLM 根据 Tool 结果继续推理或生成最终回复
  → 循环直到 LLM 认为任务完成
```

**与原方案的差异**：

- 原方案：预定义 DAG，步骤硬编码 → 新场景需要加新 DAG
- nanobot：LLM 自主编排工具调用 → Skill 提供领域指导，Tool 提供能力边界
- **约束靠 Skill prompt 和 Tool schema，而非硬编码流程**

### 3.2 Skills（替代 Intent Router）

nanobot 的 Skill = **一个目录，包含 prompt 和可选工具**，用于给 Agent 注入领域知识。

```
workspace/skills/
├── stock/              # 股票分析技能
│   └── skill.md        # 告诉 LLM 如何分析股票、可用工具、输出格式
├── fund/               # 基金分析技能
│   └── skill.md
├── screening/          # 条件筛选技能
│   └── skill.md
├── backtest/           # 回测技能
│   └── skill.md
└── market/             # 市场概览技能
    └── skill.md
```

Skill prompt 示例 (`skills/stock/skill.md`)：

```markdown
## 股票分析

当用户询问个股相关问题时，使用以下工具组合：

1. 用 `stock_resolve` 解析股票代码
2. 用 `stock_quote` 获取实时行情
3. 用 `stock_indicators` 计算技术指标（MA/MACD/RSI/KDJ）
4. 用 `stock_fundamentals` 获取基本面数据（PE/PB/ROE）

分析规则：
- 所有数值直接引用工具返回值，不要自行计算或估算
- 不给出买卖建议，只做客观分析
- 结尾附加：「以上分析仅供参考，不构成投资建议。」
- 如果用户追问"换周线/月线"，从上下文获取标的代码
```

**这替代了原方案的 Intent Router + Workflow Engine**：

- 不需要手动维护意图清单和路由规则
- LLM 阅读 Skill prompt 后自然知道何时用什么工具
- 新增能力 = 新增 Skill 目录 + 对应 Tool，零改动 Agent 代码

### 3.3 Tools（通过 MCP 注册金融工具）

XShare 的核心工具以 **MCP Server** 形式注册到 nanobot Agent Loop 中。

nanobot 启动时根据 `config.json` 拉起 MCP Server 子进程，通过 stdio 通信，自动发现并注册所有工具：

```json
// ~/.nanobot/config.json
{
  "tools": {
    "mcpServers": {
      "xshare": {
        "command": "python",
        "args": ["-m", "xshare.mcp_server"],
        "toolTimeout": 30
      }
    }
  }
}
```

运行时进程关系：

```
nanobot gateway (主进程)
  └── python -m xshare.mcp_server (子进程, stdio)
       ├── stock_resolve
       ├── stock_quote
       ├── stock_indicators
       └── ... 所有金融工具
```

**选择 MCP 的理由**：

- 解耦：XShare 工具独立进程，不侵入 nanobot 代码
- 标准化：MCP 是通用协议，工具可复用于 Claude Desktop / Cursor 等
- 调试方便：可单独启动 MCP Server 测试
- Gateway 模式原生支持，无需写 Python 胶水代码

> **未来演进**：如果 Gateway + MCP 模式无法满足需求（如需拦截 Agent Loop、动态控制工具列表），
> 可切换到 SDK 模式，用 `Nanobot.from_config()` + `AgentHook` 在 XShare 主进程中嵌入 nanobot。
> MCP Server 代码无需改动，只需调整启动方式。

### 3.4 Channels（替代自研消息网关）

nanobot 内置多通道支持，**微信接入零代码**：

```bash
# 微信（个人号）—— 扫码登录即可
nanobot channels login weixin

# 企业微信
# 在 config.json 中配置 wecom channel

# 启动 Gateway（同时服务所有已配置通道）
nanobot gateway
```

配置示例 (`~/.nanobot/config.json`)：

```json
{
  "channels": {
    "weixin": {
      "enabled": true,
      "allowFrom": ["wxid_friend1", "wxid_friend2"]
    },
    "wecom": {
      "enabled": true,
      "botId": "xxx",
      "botSecret": "xxx"
    },
    "telegram": {
      "enabled": false,
      "token": "YOUR_BOT_TOKEN"
    }
  }
}
```

**关键能力（nanobot 已内置）**：

- 消息去重 & 会话隔离（按 channel:chat_id）
- 语音消息自动转文字（Whisper，通过 Groq 免费层）
- 图片/文件接收与处理
- 发送进度流式推送（`sendProgress: true`）
- 重试机制（`sendMaxRetries: 3`，指数退避）
- `allowFrom` 白名单控制访问

### 3.5 Session & Memory（替代自研会话管理）

nanobot 提供分层记忆系统：

```
短期记忆 ──→ session.messages (当前对话)
             │
             │ 上下文过大时
             ▼
中期记忆 ──→ memory/history.jsonl (压缩摘要，append-only)
             │
             │ Dream 定期整理
             ▼
长期记忆 ──→ SOUL.md (Agent 人设/风格)
             USER.md (用户偏好)
             memory/MEMORY.md (持久知识)
```

**XShare 场景下的 Memory 利用**：

| Memory 文件 | 用途 |
|------------|------|
| `SOUL.md` | 定义 Agent 人设："你是一个专业但友好的金融分析助手..." |
| `USER.md` | 自动学习用户偏好：关注的行业、风险偏好、常看的标的 |
| `MEMORY.md` | 持久化关键信息：用户持仓、历史分析记录、策略参数 |

配置 Auto Compact（避免上下文过长）：

```json
{
  "agents": {
    "defaults": {
      "idleCompactAfterMinutes": 15
    }
  }
}
```

### 3.6 Cron / Heartbeat（替代自研定时任务）

nanobot 内置定时任务能力，可用于：

- **盘前提醒**：每个交易日 9:15 推送关注股票的集合竞价情况
- **持仓异动**：盘中定期检查用户关注标的的异常波动
- **收盘总结**：每日 15:05 推送当日市场概览 + 持仓变动

---

## 4. XShare 领域层详细设计

> 以下是 XShare 需要自己实现的部分。

### 4.1 MCP Tools 清单

| Tool 名称 | 功能 | 输入 | 输出 |
|-----------|------|------|------|
| `stock_resolve` | 模糊匹配股票代码 | `{query: "比亚迪"}` | `{code: "002594.SZ", name: "比亚迪"}` |
| `stock_quote` | 实时行情 | `{code: "002594.SZ"}` | `{price, change, volume, ...}` |
| `stock_indicators` | 技术指标计算 | `{code, indicators[], period}` | `{ma5, ma20, macd, rsi, ...}` |
| `stock_fundamentals` | 基本面数据 | `{code}` | `{pe, pb, roe, revenue_growth, ...}` |
| `stock_screen` | 条件筛选 | `{filters: [{field, op, value}]}` | `[{code, name, ...}, ...]` |
| `backtest_run` | 策略回测 | `{strategy, target, period}` | `{annual_return, max_drawdown, trades[]}` |
| `market_overview` | 大盘概览 | `{}` | `{sh_index, sz_index, sectors, ...}` |
| `stock_news` | 个股相关新闻 | `{code?, keyword?, days?}` | `[{title, time, source, summary}, ...]` |

所有工具遵循 **MCP 协议** 注册，LLM 通过 function calling 调用。

### 4.2 数据层

| 数据类型 | 来源 | 缓存策略 |
|---------|------|---------|
| 实时行情 | 仅 AkShare | 直连 AkShare；失败回退 `stock_daily` 最近收盘（`is_delayed`）；不回退 Tushare |
| 日线历史 | Tushare（批量 sync） | DuckDB `stock_daily` + watermark；读路径 local-first |
| 指数基础 / 日线 | Tushare（批量 sync） | DuckDB `index_basic` / `index_daily`；日历任务同股票日线 |
| ETF 基础 / 日线 | Tushare（批量 sync） | DuckDB `etf_basic` / `fund_daily`；日历任务同股票日线 |
| 每日指标 | Tushare（批量 sync） | DuckDB `stock_daily_basic` |
| 基金净值 | Tushare / AkShare sync | DuckDB `fund_nav`；读路径 local-first |
| 财务数据 | Tushare sync | DuckDB `stock_finance`；读路径 local-first |
| 交易日历 | Tushare sync | DuckDB `trade_cal` |
| 公告/新闻 | 同花顺 | DuckDB `news`，interval 定时拉取 |

**数据校验规则**（在 Tool 内部执行，不经过 LLM）：

- 价格 ≤ 0 → 丢弃并告警
- 涨跌幅 > 20%（非 ST/新股）→ 标记异常
- 时间戳不连续 → 补全交易日历

### 4.3 指标计算引擎

纯 Python 实现，作为 Tool 的内部实现：

- **技术指标**：MA / EMA / MACD / RSI / KDJ / BOLL / ATR
- **基本面指标**：PE / PB / ROE / 股息率 / 营收增速
- **基金指标**：年化收益 / 最大回撤 / 夏普比率 / 信息比率 / 持仓集中度

计算库：`pandas`（纯 pandas 实现避免 ta-lib C 依赖）

### 4.3.1 新闻数据存储

Cron 定时拉取，本地 DuckDB 存储，支持按标的/关键词检索：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | VARCHAR | 新闻唯一标识（URL hash） |
| `publish_time` | TIMESTAMP | 发布时间 |
| `source` | VARCHAR | 来源（东财/新浪/同花顺） |
| `title` | VARCHAR | 标题 |
| `content` | VARCHAR | 正文摘要（前 500 字） |
| `stock_codes` | VARCHAR[] | 关联股票代码 |
| `tags` | VARCHAR[] | 标签（行业/事件类型） |

- **拉取频率**：交易日 9:00 / 12:00 / 18:00，非交易日 18:00
- **保留策略**：滚动保留近 90 天，过期自动清理
- **去重**：按 URL hash 去重，避免重复入库
- **查询场景**：分析个股时自动关联近期新闻；用户主动问"最近有什么消息"

### 4.4 回测引擎

MVP 版本的轻量回测（`backtest_run` Tool 内部）：

```
输入：策略定义（信号规则 + 仓位管理） + 标的 + 时间范围
输出：收益曲线、年化收益、最大回撤、夏普比率、交易记录
```

- 内置策略模板：均线交叉、RSI 超买超卖、网格策略
- 用户自然语言描述策略 → LLM 转成结构化策略定义 → Tool 执行
- **LLM 不参与回测计算本身**

### 4.5 风控模块

硬编码在 Skill prompt + Tool 输出后处理中：

- 免责声明：写在 Skill prompt 中，LLM 每次分析后自动附加
- 数据校验：在 Tool 内部执行，返回前过滤异常
- 禁止行为：Skill prompt 明确禁止给出买卖点建议
- 回测声明：`backtest_run` 输出自动附加"历史不代表未来"

---

## 5. 数据流示例

### 场景："帮我分析一下比亚迪"

```
[微信用户] "帮我分析一下比亚迪"
   │
   ▼
[nanobot Gateway / weixin channel]
   → 消息标准化 → 路由到 Agent Session
   │
   ▼
[nanobot Agent Loop]
   │
   │  System Prompt = SOUL.md + Skills(stock/fund/...) + USER.md 上下文
   │  User Message = "帮我分析一下比亚迪"
   │
   │  ── Iteration 1 ──
   │  LLM 阅读 stock skill → 决定调用工具
   │  → tool_call: stock_resolve({query: "比亚迪"})
   │  → tool_call: stock_quote({code: "002594.SZ"})      ← 可并行
   │  → tool_call: stock_indicators({code: "002594.SZ",
   │               indicators: ["MA","MACD","RSI"], period: "daily"})
   │  → tool_call: stock_fundamentals({code: "002594.SZ"})
   │
   │  [nanobot 执行 Tools，通过 MCP 调用 XShare]
   │  ← 返回结构化数据
   │
   │  ── Iteration 2 ──
   │  LLM 拿到全部数据 → 按 Skill prompt 的格式要求生成分析报告
   │  → 最终回复（附免责声明）
   │
   ▼
[nanobot Gateway / weixin channel]
   → 发送微信消息给用户
```

---

## 6. 技术栈选型 (MVP)

| 层 | 选型 | 理由 |
|----|------|------|
| Agent 框架 | **nanobot** (`pip install nanobot-ai`) | Agent Loop + Channel + Memory 全家桶 |
| 语言 | **Python 3.11+** | nanobot 要求 ≥ 3.11 |
| LLM | **通过 nanobot Provider 配置** | OpenRouter / DeepSeek / Ollama / ... |
| 微信接入 | **nanobot weixin channel** | 内置，扫码即用 |
| 工具协议 | **MCP (Model Context Protocol)** | 标准化，可复用 |
| 数据获取 | **AKShare + Tushare** | 免费 + 覆盖面广 |
| 存储 | **DuckDB** | 嵌入式列存储，OLAP 查询快 10-100x，零部署 |
| 指标计算 | **pandas** | 轻量，避免 ta-lib C 依赖 |
| 配置 | **nanobot config.json + pydantic** | nanobot 统一配置 |

---

## 7. 项目结构

```
xshare/
├── docs/                          # 文档
│   └── architecture.md
├── workspace/                     # nanobot workspace 目录
│   ├── SOUL.md                    # Agent 人设定义
│   ├── USER.md                    # 用户信息（Dream 自动维护）
│   ├── memory/
│   │   └── MEMORY.md              # 长期记忆（Dream 自动维护）
│   └── skills/                    # XShare 领域技能
│       ├── stock/
│       │   └── skill.md           # 股票分析 Skill prompt
│       ├── fund/
│       │   └── skill.md           # 基金分析 Skill prompt
│       ├── screening/
│       │   └── skill.md           # 条件筛选 Skill prompt
│       ├── backtest/
│       │   └── skill.md           # 回测 Skill prompt
│       └── market/
│           └── skill.md           # 市场概览 Skill prompt
├── xshare/
│   ├── __init__.py
│   ├── cli.py                 # CLI 子命令入口（db/portfolio/news/serve）
│   ├── mcp_server.py          # MCP Server 入口
│   ├── tools/                 # MCP Tool 实现
│   │   ├── stock_resolve.py   # 股票代码解析
│   │   ├── stock_quote.py     # 实时行情
│   │   ├── stock_indicators.py # 技术指标
│   │   ├── stock_fundamentals.py # 基本面
│   │   ├── screener.py        # 条件筛选
│   │   ├── backtest.py        # 回测引擎
│   │   └── market_overview.py # 大盘概览
│   ├── data/                  # 数据层
│   │   ├── sources/           # 数据源适配 (AKShare/Tushare)
│   │   ├── db.py              # DuckDB 连接 & 表管理
│   │   ├── news.py            # 新闻拉取 & 存储
│   │   └── validator.py       # 数据校验
│   └── indicators/            # 指标计算
│       ├── technical.py       # 技术指标 (MA/MACD/RSI/...)
│       └── fundamental.py     # 基本面指标
├── config/
│   └── nanobot.example.json       # nanobot 配置示例
├── tests/
├── scripts/
│   └── fetch_tushare_docs.py     # 辅助脚本（Tushare 文档抓取）
└── pyproject.toml
```

---

## 8. 配置示例

### 8.1 nanobot 主配置 (`~/.nanobot/config.json`)

```json
{
  "agents": {
    "defaults": {
      "model": "deepseek/deepseek-chat",
      "provider": "openrouter",
      "workspace": "~/workspace/xshare/workspace",
      "timezone": "Asia/Shanghai",
      "idleCompactAfterMinutes": 15,
      "dream": {
        "intervalH": 4
      }
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "channels": {
    "weixin": {
      "enabled": true,
      "allowFrom": ["*"]
    }
  },
  "tools": {
    "mcpServers": {
      "xshare": {
        "command": "python",
        "args": ["-m", "xshare.mcp_server"],
        "toolTimeout": 30
      }
    },
    "web": {
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```

### 8.2 SOUL.md（Agent 人设）

```markdown
你是 XShare，一个专业、客观的金融分析助手。

## 风格
- 说话简洁专业，不使用过于花哨的表达
- 数据先行：先给数字，再做解读
- 诚实面对不确定性，不强行解读

## 硬规则
- 所有数值必须来自工具返回结果，绝不自行编造或估算
- 每次个股/基金分析结尾附加免责声明
- 不给出明确的买入/卖出建议
- 回测结果必须注明"历史业绩不代表未来表现"
```

---

## 9. 快速启动流程

```bash
# 1. 安装 nanobot
pip install nanobot-ai

# 2. 初始化 nanobot（指定 workspace 为 xshare 项目中的 workspace 目录）
nanobot onboard --workspace ~/workspace/xshare/workspace

# 3. 安装 XShare 工具（确保 nanobot 子进程能 import xshare）
cd ~/workspace/xshare
pip install -e .

# 4. 配置 (编辑 ~/.nanobot/config.json，参考 8.1)
# - 设置 LLM provider & API key
# - 设置 workspace 指向 ~/workspace/xshare/workspace
# - 启用 weixin channel
# - 添加 xshare MCP server

# 5. 微信登录
nanobot channels login weixin  # 扫码

# 6. 启动 Gateway
nanobot gateway

# 或者 CLI 模式快速测试（不需要微信）
nanobot agent -m "帮我看看茅台"
```

> **注意**：`pip install -e .` 和 `pip install nanobot-ai` 必须在同一个 Python 环境中，
> 否则 nanobot 拉起的 MCP 子进程无法 `import xshare`。

---

## 10. MVP 分阶段路线

### Phase 1 — 能跑

- [ ] nanobot 环境搭建 & 配置
- [ ] 微信 channel 联通（`nanobot channels login weixin`）
- [ ] MCP Server 骨架 + `stock_resolve` + `stock_quote` 两个基础 Tool
- [ ] `skills/stock/skill.md` 基础 Skill prompt
- [ ] SOUL.md 人设 + 免责声明

### Phase 2 — 能用

- [ ] `stock_indicators` 技术指标 Tool (MA/MACD/RSI)
- [ ] `stock_fundamentals` 基本面 Tool
- [ ] `stock_screen` 条件筛选 Tool
- [ ] 完善各领域 Skill prompt
- [x] 本地 DuckDB 数据缓存 + 新闻定时拉取 + watermark / local-first

### Phase 3 — 好用

- [ ] `backtest_run` 回测 Tool
- [ ] `market_overview` 大盘概览 Tool
- [ ] Cron 定时任务：盘前提醒 / 收盘总结
- [ ] 图表生成（matplotlib → 图片发送）
- [ ] Dream 记忆调优：学习用户偏好标的/行业

---

## 11. 风险 & 约束

| 风险 | 应对 |
|------|------|
| 微信封号 | nanobot weixin channel 基于 hook，有风险；备选 wecom / Telegram channel |
| nanobot 版本变动 | 锁定版本，关注 release notes |
| 数据源不稳定 | 多源降级：AKShare → Tushare → 东财爬虫 |
| LLM 幻觉 | 数值结论全部来自 Tool，Skill prompt 强制引用工具结果 |
| LLM 乱调工具 | Skill prompt 明确约束 + Tool schema 严格校验输入 |
| 合规风险 | SOUL.md 硬规则 + Tool 输出自动附加声明 |
| Token 成本 | 用 DeepSeek（便宜）；Auto Compact 压缩上下文；Dream 用 intervalH=4 降低频率 |
| Gateway 模式不够灵活 | 先用 Gateway + MCP 跑通；如需深度定制 Agent 行为，切换 SDK 模式（MCP 代码复用） |
