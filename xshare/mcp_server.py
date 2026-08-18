"""XShare MCP Server - 金融数据工具集"""

import argparse
import asyncio
import json
import logging
import os
import re

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from xshare.tools.stock_resolve import stock_resolve
from xshare.tools.stock_quote import stock_quote
from xshare.tools.stock_indicators import stock_indicators
from xshare.tools.stock_fundamentals import stock_fundamentals
from xshare.tools.stock_news import stock_news
from xshare.tools.stock_moneyflow import stock_moneyflow
from xshare.tools.screener import stock_screen
from xshare.tools.backtest import backtest_run
from xshare.tools.market_overview import market_overview
from xshare.tools.market_mainline import market_mainline
from xshare.tools.market_top_movers import market_top_movers
from xshare.tools.market_sectors import market_sectors
from xshare.tools.portfolio import portfolio_update, portfolio_summary
from xshare.tools.sync_job import sync_job
from xshare.tools.doc_parse import doc_parse
from xshare.tools.trend_scanner import trend_scanner

logger = logging.getLogger(__name__)

app = Server("xshare")

# 单个工具调用的硬超时（秒）：即便某工具内部卡在网络/数据源，也不会
# 无限期占用事件循环、拖垮其它工具。trend_scanner 等会把阻塞核心丢到
# to_thread，使该超时能真正触发。
TOOL_TIMEOUT = float(os.environ.get("XSHARE_TOOL_TIMEOUT", "120"))

# Tool 注册表：name -> (handler, schema)
TOOLS: dict[str, tuple] = {
    "stock_resolve": (stock_resolve, {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "股票名称、代码或关键词"}
        },
        "required": ["query"],
    }),
    "stock_quote": (stock_quote, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码，如 002594.SZ"}
        },
        "required": ["code"],
    }),
    "stock_indicators": (stock_indicators, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"},
            "indicators": {
                "type": "array",
                "items": {"type": "string", "enum": ["MA", "EMA", "MACD", "RSI", "KDJ", "BOLL", "ATR", "VOL_MA", "OBV", "VWAP", "DMI", "NINE_TURN", "TREND"]},
                "description": "需要计算的技术指标列表",
            },
            "period": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "K线周期",
                "default": "daily",
            },
        },
        "required": ["code", "indicators"],
    }),
    "stock_fundamentals": (stock_fundamentals, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"}
        },
        "required": ["code"],
    }),
    "stock_news": (stock_news, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码（可选）"},
            "keyword": {"type": "string", "description": "搜索关键词（可选）"},
            "days": {"type": "integer", "description": "查询最近几天的新闻", "default": 7},
        },
    }),
    "stock_moneyflow": (stock_moneyflow, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"},
            "days": {"type": "integer", "description": "查询最近几个交易日的资金流向", "default": 10},
        },
        "required": ["code"],
    }),
    "stock_screen": (stock_screen, {
        "type": "object",
        "properties": {
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {"type": "string", "enum": ["<", ">", "<=", ">=", "==", "!="]},
                        "value": {"type": "number"},
                    },
                    "required": ["field", "op", "value"],
                },
                "description": "筛选条件列表",
            },
            "sector": {"type": "string", "description": "行业/板块筛选（可选）"},
            "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
        },
        "required": ["filters"],
    }),
    "backtest_run": (backtest_run, {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "object",
                "description": "策略定义（信号规则+仓位管理）",
                "properties": {
                    "name": {"type": "string"},
                    "rules": {"type": "array", "items": {"type": "object"}},
                },
            },
            "target": {"type": "string", "description": "回测标的代码"},
            "start_date": {"type": "string", "description": "开始日期 YYYYMMDD"},
            "end_date": {"type": "string", "description": "结束日期 YYYYMMDD"},
        },
        "required": ["strategy", "target", "start_date", "end_date"],
    }),
    "market_overview": (market_overview, {
        "type": "object",
        "properties": {},
    }),
    "market_mainline": (market_mainline, {
        "type": "object",
        "properties": {
            "sector_top_n": {"type": "integer", "description": "识别主线时返回的板块数量", "default": 8},
            "strong_limit": {"type": "integer", "description": "返回强势股数量", "default": 10},
        },
    }),
    "market_top_movers": (market_top_movers, {
        "type": "object",
        "properties": {
            "top_n": {"type": "integer", "description": "涨跌幅榜数量", "default": 5},
        },
    }),
    "market_sectors": (market_sectors, {
        "type": "object",
        "properties": {
            "top_n": {"type": "integer", "description": "板块涨跌排行数量", "default": 5},
        },
    }),
    "portfolio_update": (portfolio_update, {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["buy", "sell", "delete"],
                "description": "操作类型：buy 买入，sell 卖出，delete 删除记录",
                "default": "buy",
            },
            "code": {"type": "string", "description": "股票代码"},
            "name": {"type": "string", "description": "股票名称（可选，自动补全）"},
            "trade_date": {"type": "string", "description": "交易日期 YYYY-MM-DD（默认今天）"},
            "price": {"type": "number", "description": "成交价格"},
            "quantity": {"type": "integer", "description": "成交数量（股）"},
            "memo": {"type": "string", "description": "备注（可选）"},
            "id": {"type": "integer", "description": "记录ID（仅 delete 时可用，精确删除单笔）"},
        },
    }),
    "portfolio_summary": (portfolio_summary, {
        "type": "object",
        "properties": {},
    }),
    "sync_job": (sync_job, {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status", "config", "enqueue", "run", "history",
                    "coverage", "watermarks", "cancel", "cleanup",
                ],
                "description": (
                    "status=状态/队列/水位; config=启停/间隔; enqueue/run=入队; "
                    "history=历史; coverage=日线覆盖率; watermarks=水位明细; cancel=取消; cleanup=清理日志"
                ),
            },
            "job": {
                "type": "string",
                "enum": [
                    "news", "stock_basic", "daily", "index_basic", "index_daily",
                    "etf_basic", "fund_daily",
                    "trade_cal", "daily_basic", "finance", "fund_nav", "quote", "all",
                ],
                "default": "all",
                "description": "目标任务（status/enqueue/history 可用 all）",
            },
            "enabled": {"type": "boolean", "description": "（config）是否启用"},
            "interval_minutes": {"type": "integer", "description": "（config）同步间隔（分钟）"},
            "days": {"type": "integer", "description": "（enqueue daily/index_daily/fund_daily）同步最近 N 个交易日"},
            "pages": {"type": "integer", "description": "（enqueue news）抓取页数"},
            "retain_days": {"type": "integer", "description": "（enqueue news / cleanup）保留天数"},
            "backfill": {"type": "boolean", "description": "（enqueue daily/index_daily/fund_daily）历史补数，忽略 17:00 窗口"},
            "task_id": {"type": "integer", "description": "（cancel）任务 ID"},
            "retain_count": {"type": "integer", "description": "（cleanup）每类最少保留条数", "default": 500},
            "lookback_trading_days": {"type": "integer", "description": "（coverage）目标交易日数"},
            "dataset": {"type": "string", "description": "（watermarks）数据集过滤"},
            "limit": {"type": "integer", "description": "（history/watermarks/finance）条数限制", "default": 20},
            "force": {"type": "boolean", "description": "（enqueue finance/stock_basic/index_basic/etf_basic）强制刷新"},
            "years": {"type": "integer", "description": "（enqueue trade_cal）回溯年数"},
            "code": {"type": "string", "description": "一次性历史同步的股票、指数或 ETF 代码"},
            "start_date": {"type": "string", "description": "（enqueue daily/index_daily/fund_daily）一次性补全起始日 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "（enqueue daily/index_daily/fund_daily）一次性补全结束日 YYYY-MM-DD"},
            "overwrite": {"type": "boolean", "description": "（enqueue daily/index_daily/fund_daily）覆盖已有数据，默认 false"},
        },
        "required": ["action"],
    }),
    "trend_scanner": (trend_scanner, {
        "type": "object",
        "properties": {
            "top_n": {"type": "integer", "description": "返回趋势个股数量", "default": 30},
            "top_sectors": {"type": "integer", "description": "返回趋势行业数量", "default": 10},
            "force_provider": {"type": "boolean", "description": "强制从数据源拉取（忽略本地缓存）", "default": False},
        },
    }),
    "doc_parse": (doc_parse, {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径（PDF/JPEG/PNG）"},
            "file_base64": {"type": "string", "description": "Base64 编码的文件内容（与 file_path 二选一）"},
            "file_name": {"type": "string", "description": "文件名（仅 base64 模式时需要）", "default": "upload.pdf"},
            "pipeline_id": {"type": "string", "description": "MinerU Pipeline ID（可通过环境变量 MINERU_PIPELINE_ID 设置）"},
            "timeout": {"type": "integer", "description": "解析超时时间（秒）", "default": 120},
        },
    }),
}


@app.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用工具"""
    tools = []
    for name, (_, schema) in TOOLS.items():
        tools.append(Tool(
            name=name,
            description=_get_tool_description(name),
            inputSchema=schema,
        ))
    return tools


# ─── 参数清洗 ────────────────────────────────────────────────────────────────

_CODE_RE = re.compile(r"^\d{6}(\.(SH|SZ|BJ))?$")
_VALID_PERIOD = {"daily", "weekly", "monthly"}


def _normalize_code(raw: str) -> str:
    """标准化股票代码：去除噪音字符，补全交易所后缀"""
    code = re.sub(r"[{}\[\]\"'\s]", "", raw).upper()
    # 处理 SH600000 / SZ002594 前缀格式
    m = re.match(r"^(SH|SZ|BJ)(\d{6})$", code)
    if m:
        code = f"{m.group(2)}.{m.group(1)}"
    # 无后缀时按首位数字补全交易所
    if re.match(r"^\d{6}$", code):
        if code.startswith("6") or code.startswith("5"):
            code = f"{code}.SH"
        elif code.startswith(("0", "3")):
            code = f"{code}.SZ"
        elif code.startswith(("4", "8")):
            code = f"{code}.BJ"
    return code


def _sanitize_arguments(name: str, arguments: dict | None) -> tuple[dict, str | None]:
    """清洗工具参数；返回 (cleaned_args, error_msg_or_None)"""
    if arguments is None:
        args = {}
    elif isinstance(arguments, dict):
        args = dict(arguments)
    else:
        msg = json.dumps({
            "error": "无效参数：arguments 必须是 JSON 对象",
            "retry_same_args": False,
            "hint": "请检查工具调用参数格式，确保为 object/dict",
        }, ensure_ascii=False)
        return {}, msg

    if name == "portfolio_update":
        action = str(args.get("action", "buy")).strip().lower() or "buy"
        args["action"] = action

        if action == "delete":
            if args.get("id") is not None and not args.get("code"):
                args.pop("code", None)
            if args.get("id") is None and not args.get("code"):
                msg = json.dumps({
                    "error": "portfolio_update delete 需要提供 id 或 code",
                    "retry_same_args": False,
                    "hint": "删除单笔请传 id；删除某标的全部记录请传 code",
                }, ensure_ascii=False)
                return args, msg
        elif not args.get("code"):
            msg = json.dumps({
                "error": "portfolio_update buy/sell 需要提供 code",
                "retry_same_args": False,
                "hint": "请补充股票代码，或先调用 stock_resolve 获取正确代码",
            }, ensure_ascii=False)
            return args, msg

    if "code" in args:
        code = _normalize_code(str(args["code"]))
        if not _CODE_RE.match(code):
            msg = json.dumps({
                "error": f"无效的股票代码 '{args['code']}'，请先用 stock_resolve 确认正确代码",
                "retry_same_args": False,
                "hint": "调用 stock_resolve 搜索正确的股票代码，不要重复使用相同参数",
            }, ensure_ascii=False)
            return args, msg
        args["code"] = code

    if "period" in args:
        period = re.sub(r"[{}\[\]\"'\s]", "", str(args["period"])).lower()
        args["period"] = period if period in _VALID_PERIOD else "daily"

    # stock_indicators: indicators 是必填项，缺失时提供默认值避免 KeyError
    if name == "stock_indicators" and not args.get("indicators"):
        args["indicators"] = ["MA", "MACD", "RSI", "KDJ", "BOLL", "TREND"]

    return args, None


@app.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """执行工具调用"""
    if name not in TOOLS:
        return [TextContent(type="text", text=f"未知工具: {name}")]

    try:
        args, err = _sanitize_arguments(name, arguments)
        if err:
            return [TextContent(type="text", text=err)]

        handler, _ = TOOLS[name]
        result = await asyncio.wait_for(handler(args), timeout=TOOL_TIMEOUT)
        return [TextContent(type="text", text=result)]
    except asyncio.TimeoutError:
        logger.warning("工具 %s 执行超时（%ss）", name, TOOL_TIMEOUT)
        return [TextContent(
            type="text",
            text=json.dumps(
                {
                    "error": f"工具 {name} 执行超时（{int(TOOL_TIMEOUT)}s），已中止",
                    "timeout": True,
                    "retry_same_args": False,
                    "hint": "数据源响应过慢；可稍后重试或 force_provider 切换路径",
                },
                ensure_ascii=False,
            ),
        )]
    except Exception as e:
        logger.exception("工具 %s 执行异常", name)
        return [TextContent(
            type="text",
            text=json.dumps(
                {
                    "error": f"工具执行错误 [{name}]: {e}",
                    "retry_same_args": False,
                },
                ensure_ascii=False,
            ),
        )]


def _get_tool_description(name: str) -> str:
    """工具描述映射"""
    descriptions = {
        "stock_resolve": "模糊匹配股票代码，支持名称、拼音、代码片段",
        "stock_quote": "获取股票实时行情（价格、涨跌幅、成交量等）",
        "stock_indicators": "计算股票技术指标（MA/MACD/RSI/KDJ/BOLL/ATR）",
        "stock_fundamentals": "获取股票基本面数据（PE/PB/ROE/营收增速等）",
        "stock_news": "查询个股相关新闻，支持按代码或关键词检索",
        "stock_moneyflow": "查询个股资金流向（四档净额：散户/中户/大户/机构 + 背离标签）",
        "stock_screen": "按条件筛选股票（PE/PB/ROE/行业等多维度筛选）",
        "backtest_run": "策略回测（均线交叉、RSI等策略的历史回测）",
        "market_overview": "大盘概览（主要指数、板块涨跌、市场情绪）",
        "market_mainline": "识别市场主线方向与强势股（主线板块、市场阶段、强势个股）",
        "market_top_movers": "涨跌幅榜 Top N（涨幅榜与跌幅榜）",
        "market_sectors": "板块涨跌排行 Top N",
        "portfolio_update": "管理用户持仓（添加/删除持仓记录，含买入价格和数量）",
        "portfolio_summary": "查看用户持仓概览（持仓列表、成本、仓位占比）",
        "sync_job": "管理定时同步任务（查看状态/修改间隔与启停/立即触发同步，热生效）",
        "trend_scanner": "扫描正在走趋势的股票和行业（20/60日动量 + 均线排列 + 量能放大综合评分）",
        "doc_parse": "解析文档或图片（PDF/JPEG/PNG），提取文字、表格、公式等内容，基于 MinerU KIE",
    }
    return descriptions.get(name, "")


def parse_args():
    parser = argparse.ArgumentParser(description="XShare MCP Server")
    parser.add_argument(
        "--tushare-token",
        default=os.environ.get("TUSHARE_TOKEN", ""),
        help="Tushare Pro API token (也可通过 TUSHARE_TOKEN 环境变量设置)",
    )
    return parser.parse_args()


# ─── 后台定时同步（委托给 xshare.data.sync_config）──────────────────────────

async def run_server(token: str = ""):
    """MCP Server 运行核心：供 `python -m xshare.mcp_server` 与 `xshare serve` 共用"""
    from dotenv import load_dotenv
    load_dotenv()  # 自动读取项目根目录 .env

    # 日志输出到 stderr（stdout 留给 MCP JSON-RPC 协议）
    from xshare.logging_config import configure_logging
    configure_logging(force=True)

    if token:
        os.environ["TUSHARE_TOKEN"] = token

    # 1. 无条件初始化数据库 + 同步任务配置（幂等）
    from xshare.data.db import init_tables
    from xshare.data.sqlite_db import init_sqlite_tables
    from xshare.data.sync_config import init_sync_config
    init_tables()
    init_sqlite_tables()
    init_sync_config()
    logger.info("数据库表结构与同步配置已就绪")

    # 2. 启动后台同步运行时（worker + timer）；服务即时可用，首次同步由 worker 后台完成
    from xshare.data.sync_runtime import shutdown_sync_runtime, spawn_sync_runtime

    sync_tasks = spawn_sync_runtime()

    # 3. 启动 MCP stdio 服务，退出时清理后台任务
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        await shutdown_sync_runtime(sync_tasks)
        # 关闭数据库连接
        from xshare.data.db import close
        from xshare.data.sqlite_db import close_sqlite
        close()
        close_sqlite()


async def main():
    args = parse_args()
    await run_server(token=args.tushare_token)


if __name__ == "__main__":
    asyncio.run(main())
