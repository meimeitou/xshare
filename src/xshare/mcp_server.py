"""XShare MCP Server - 金融数据工具集"""

import argparse
import asyncio
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from xshare.tools.stock_resolve import stock_resolve
from xshare.tools.stock_quote import stock_quote
from xshare.tools.stock_indicators import stock_indicators
from xshare.tools.stock_fundamentals import stock_fundamentals
from xshare.tools.stock_news import stock_news
from xshare.tools.fund_info import fund_info
from xshare.tools.fund_analysis import fund_analysis
from xshare.tools.screener import stock_screen
from xshare.tools.backtest import backtest_run
from xshare.tools.market_overview import market_overview
from xshare.tools.portfolio import portfolio_update, portfolio_summary
from xshare.tools.sync_news import sync_news
from xshare.tools.doc_parse import doc_parse

app = Server("xshare")

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
                "items": {"type": "string", "enum": ["MA", "EMA", "MACD", "RSI", "KDJ", "BOLL", "ATR"]},
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
    "fund_info": (fund_info, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "基金代码，如 110011"}
        },
        "required": ["code"],
    }),
    "fund_analysis": (fund_analysis, {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "基金代码"},
            "period": {"type": "string", "description": "分析周期，如 1y/3y/5y", "default": "1y"},
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
        "required": ["code"],
    }),
    "portfolio_summary": (portfolio_summary, {
        "type": "object",
        "properties": {},
    }),
    "sync_news": (sync_news, {
        "type": "object",
        "properties": {
            "pages": {"type": "integer", "description": "抓取页数（1-10）", "default": 3},
            "retain_days": {"type": "integer", "description": "新闻保留天数", "default": 1},
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


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """执行工具调用"""
    if name not in TOOLS:
        return [TextContent(type="text", text=f"未知工具: {name}")]

    handler, _ = TOOLS[name]
    try:
        result = await handler(arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=f"工具执行错误 [{name}]: {e}")]


def _get_tool_description(name: str) -> str:
    """工具描述映射"""
    descriptions = {
        "stock_resolve": "模糊匹配股票代码，支持名称、拼音、代码片段",
        "stock_quote": "获取股票实时行情（价格、涨跌幅、成交量等）",
        "stock_indicators": "计算股票技术指标（MA/MACD/RSI/KDJ/BOLL/ATR）",
        "stock_fundamentals": "获取股票基本面数据（PE/PB/ROE/营收增速等）",
        "stock_news": "查询个股相关新闻，支持按代码或关键词检索",
        "fund_info": "获取基金基础信息（名称、类型、规模、基金经理等）",
        "fund_analysis": "基金绩效分析（年化收益、最大回撤、夏普比率等）",
        "stock_screen": "按条件筛选股票（PE/PB/ROE/行业等多维度筛选）",
        "backtest_run": "策略回测（均线交叉、RSI等策略的历史回测）",
        "market_overview": "大盘概览（主要指数、板块涨跌、市场情绪）",
        "portfolio_update": "管理用户持仓（添加/删除持仓记录，含买入价格和数量）",
        "portfolio_summary": "查看用户持仓概览（持仓列表、成本、仓位占比）",
        "sync_news": "同步同花顺 7×24 实时新闻到本地数据库，并清理过期新闻",
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


async def main():
    from dotenv import load_dotenv
    load_dotenv()  # 自动读取项目根目录 .env

    args = parse_args()
    if args.tushare_token:
        os.environ["TUSHARE_TOKEN"] = args.tushare_token

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
