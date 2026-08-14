"""LangChain 工具适配层 — 将 xshare MCP 工具包装为 StructuredTool。"""

import json
import os
from typing import Any

import httpx
from langchain_core.tools import StructuredTool, tool

from xshare import mcp_server

# 工具白名单（不含管理类：portfolio_*, sync_job, backtest_run, doc_parse）
AI_TOOL_NAMES = [
    "stock_resolve",
    "stock_quote",
    "stock_indicators",
    "stock_fundamentals",
    "stock_news",
    "stock_screen",
    "market_overview",
    "market_mainline",
    "market_top_movers",
    "market_sectors",
    "trend_scanner",
]

WEB_SEARCH_API_KEY = os.environ.get("WEB_SEARCH_API_KEY", "")


def _make_lc_tool(name: str, handler: Any, schema: dict, description: str) -> StructuredTool:
    """将 xshare async tool 包装为 LangChain StructuredTool。

    _run 闭包复用 web_server._invoke_tool 的线程池隔离，
    确保 AkShare/Tushare/DuckDB 的阻塞 I/O 不卡住事件循环。
    """

    async def _run(**kwargs: Any) -> str:
        from xshare.web_server import _invoke_tool

        return await _invoke_tool(handler, kwargs)

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=description,
        args_schema=schema,
    )


@tool
async def web_search(query: str, topic: str = "general", days: int = 7) -> str:
    """搜索互联网获取最新新闻、政策、行业动态、外部事件等情报信息。

    用于补充行情数据之外的最新情报，例如：
    - 个股最新新闻、公告、政策影响
    - 行业趋势、市场热点、板块轮动
    - 宏观政策、国际事件对 A 股的影响

    参数:
    - query: 搜索关键词（中文或英文）
    - topic: "general" 综合搜索 / "news" 新闻搜索（默认 general）
    - days: 新闻时间范围，默认近 7 天（仅 topic=news 时生效）
    """
    if not WEB_SEARCH_API_KEY:
        return json.dumps({"error": "网络搜索未配置", "results": []}, ensure_ascii=False)

    payload: dict[str, Any] = {
        "api_key": WEB_SEARCH_API_KEY,
        "query": query,
        "max_results": 5,
        "topic": topic,
        "search_depth": "advanced",  # 深度搜索，获取更详细内容
        "include_answer": True,       # 让 Tavily 生成综合摘要
    }
    if topic == "news" and days > 0:
        payload["days"] = days

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"搜索失败: {e}", "results": []}, ensure_ascii=False)

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content") or "")[:500],
            "score": round(r.get("score", 0), 3),
        }
        for r in (data.get("results") or [])
    ]
    answer = data.get("answer", "")
    return json.dumps({"answer": answer, "results": results}, ensure_ascii=False)


_tools_cache: list[StructuredTool] | None = None


def build_tools() -> list[StructuredTool]:
    """构建 LangChain 工具列表，模块级缓存。"""
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache

    tools: list[StructuredTool] = []
    for name in AI_TOOL_NAMES:
        handler, schema = mcp_server.TOOLS[name]
        description = mcp_server._get_tool_description(name)
        tools.append(_make_lc_tool(name, handler, schema, description))

    tools.append(web_search)
    _tools_cache = tools
    return tools
