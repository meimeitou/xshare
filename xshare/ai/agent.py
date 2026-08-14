"""AI 问股 Agent 构建 + SSE 流式输出。

LangGraph create_react_agent + LiteLLM ChatLiteLLM。
create_react_agent 内置 ReAct 循环，AsyncSqliteSaver 提供多轮会话持久化（落盘 SQLite）。
"""

import json
import logging
import os
from pathlib import Path

import aiosqlite
from langchain_core.messages import HumanMessage
from langchain_litellm import ChatLiteLLM
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent

from xshare.ai.tools import build_tools

logger = logging.getLogger(__name__)

# ─── 模型配置 ──────────────────────────────────────────────────────────────────
LLM_API_BASE = os.environ.get("XSHARE_LLM_API_BASE", "https://api.360.cn/v1")
LLM_API_KEY = os.environ.get("XSHARE_LLM_API_KEY") or os.environ.get("OPENCODE_360ZHINAO_API_KEY", "")
LLM_MODEL = os.environ.get("XSHARE_LLM_MODEL", "z-ai/glm-5.2")

SYSTEM_PROMPT = """你是 XShare AI 问股助手，专业、客观的 A 股金融分析助手。

核心原则:
- 所有数值必须来自工具返回，绝不编造或估算
- 数据先行:先给数字,再做解读
- 综合研判:将行情数据与技术指标、基本面、最新情报相结合,形成完整分析
- 不给出明确的买入/卖出建议
- 个股分析结尾附加免责声明

工具使用规则:
- 金融行情(股价、涨跌、财务、大盘)必须用 stock_quote / market_overview / stock_fundamentals 等工具,禁止用 web_search 获取
- 个股新闻用 stock_news 工具
- 用户提到股票名称时,先调 stock_resolve 解析为代码
- 回答需要数据时直接调用工具,无需确认语

搜索与情报:
- 主动搜索:当用户询问个股或行业时,除了调金融工具获取行情/基本面数据,还应调 web_search 搜索最新新闻、政策、行业动态等情报信息,进行综合研判
- 搜索策略:topic="news" 用于近期新闻(含 days 参数控制时间范围),topic="general" 用于政策解读、行业深度等
- 分析整合:将搜索到的情报与行情数据结合,分析事件对股价/趋势的潜在影响
- 信息标注:引用搜索结果时标注来源(标题),让用户了解信息出处
- 优先级:行情数据为硬基础,搜索情报为软补充,两者结合给出完整画面"""


def _build_llm() -> ChatLiteLLM:
    return ChatLiteLLM(
        model=f"openai/{LLM_MODEL}",  # openai/ prefix = OpenAI-compatible provider
        api_base=LLM_API_BASE,  # MUST include /v1; client auto-appends /chat/completions
        api_key=LLM_API_KEY,  # explicit, do NOT overload OPENAI_API_KEY
        temperature=0.7,
        max_tokens=4096,
    )


# ─── Agent 构建（懒加载，异步初始化 SqliteSaver）─────────────────────────────
_AI_DB_PATH = os.environ.get(
    "XSHARE_AI_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "ai_sessions.sqlite"),
)

_checkpointer: AsyncSqliteSaver | None = None
_agent = None


async def _get_checkpointer() -> AsyncSqliteSaver:
    """懒加载 AsyncSqliteSaver：首次调用时打开 aiosqlite 连接 + 建表。"""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer
    Path(_AI_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(_AI_DB_PATH)
    _checkpointer = AsyncSqliteSaver(conn)
    await _checkpointer.setup()  # 幂等：创建 checkpoints / writes 表
    return _checkpointer


async def get_agent():
    """懒加载构建 agent。首次调用时初始化 AsyncSqliteSaver + create_react_agent。"""
    global _agent
    if _agent is None:
        _agent = create_react_agent(
            model=_build_llm(),
            tools=build_tools(),
            prompt=SYSTEM_PROMPT,
            checkpointer=await _get_checkpointer(),
        )
    return _agent


# ─── SSE 流式输出 ──────────────────────────────────────────────────────────────
async def chat_stream(session_id: str, user_message: str):
    """async generator，yield {"data": json_str} dict。

    流式映射:
    - updates 模式: tool_call / tool_result 事件
    - messages 模式: token 级文本增量
    - 流结束: done 事件
    """
    agent = await get_agent()
    config = {"configurable": {"thread_id": session_id}}

    try:
        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=user_message)]},
            config=config,
            stream_mode=["updates", "messages"],
            version="v2",
        ):
            if chunk["type"] == "updates":
                for _node_name, state_delta in chunk["data"].items():
                    if "messages" not in state_delta:
                        continue
                    last_msg = state_delta["messages"][-1]
                    # 模型节点产出 AIMessage with tool_calls → tool_call 事件
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        for tc in last_msg.tool_calls:
                            yield {"data": json.dumps(
                                {
                                    "type": "tool_call",
                                    "name": tc["name"],
                                    "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                                },
                                ensure_ascii=False,
                            )}
                    # 工具节点产出 ToolMessage → tool_result 事件
                    elif hasattr(last_msg, "tool_call_id"):
                        yield {"data": json.dumps(
                            {
                                "type": "tool_result",
                                "name": getattr(last_msg, "name", ""),
                                "result": str(last_msg.content)[:500],
                            },
                            ensure_ascii=False,
                        )}

            elif chunk["type"] == "messages":
                msg_chunk, metadata = chunk["data"]
                # 跳过工具节点的 messages 流 — 它们重复 updates 已发的 tool_result
                if metadata.get("langgraph_node") == "tools":
                    continue
                # GLM content 可能是 list[{"type":"thinking",...},{"type":"text","text":"..."}]
                # 只提取 text 类型的增量，跳过 thinking/reasoning
                raw = msg_chunk.content
                if isinstance(raw, list):
                    text_parts = [
                        part.get("text", "")
                        for part in raw
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    text = "".join(text_parts)
                elif isinstance(raw, str):
                    text = raw
                else:
                    text = ""
                if text:
                    yield {"data": json.dumps(
                        {"type": "token", "content": text},
                        ensure_ascii=False,
                    )}

    except Exception as e:
        logger.exception("chat_stream error")
        yield {"data": json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)}
        return

    yield {"data": json.dumps({"type": "done"}, ensure_ascii=False)}


# ─── Session 历史回显 ──────────────────────────────────────────────────────────
def _extract_text(content) -> str:
    """从 message content 提取纯文本（处理 GLM 的 list[{"type":"text","text":"..."}] 格式）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "".join(parts)
    return str(content)


async def get_sessions() -> list[dict]:
    """列出所有 session，返回 [{session_id, title, message_count, updated_at}]。

    直接查 SQLite checkpoints 表，按 thread_id 分组取最新 checkpoint。
    """
    agent = await get_agent()
    cp = await _get_checkpointer()
    sessions: list[dict] = []

    # 每个 thread 的最新 checkpoint（checkpoint_id 是递增时间戳字符串，MAX 即最新）
    async with cp.conn.execute(
        """
        SELECT thread_id, MAX(checkpoint_id) AS latest_cp
        FROM checkpoints
        WHERE checkpoint_ns = ''
        GROUP BY thread_id
        """
    ) as cur:
        thread_rows = await cur.fetchall()

    for thread_id, _latest_cp in thread_rows:
        config = {"configurable": {"thread_id": thread_id}}
        state = await agent.aget_state(config)
        msgs = state.values.get("messages", []) if state and state.values else []
        if not msgs:
            continue
        # 用第一条 HumanMessage 作为标题
        first_human = next((m for m in msgs if type(m).__name__ == "HumanMessage"), None)
        title = _extract_text(first_human.content)[:40] if first_human else "(空对话)"
        sessions.append({
            "session_id": thread_id,
            "title": title,
            "message_count": len(msgs),
        })
    # 按消息数倒序（活跃的在前）
    sessions.sort(key=lambda s: s["message_count"], reverse=True)
    return sessions


async def get_history(session_id: str) -> list[dict]:
    """获取指定 session 的完整消息历史，返回前端可直接渲染的结构。

    将 LangGraph 的多消息 ReAct 循环（AIMessage[tool_calls] + ToolMessage… +
    AIMessage[text]）折叠为单条 assistant 消息，包含 content + toolCalls。
    """
    agent = await get_agent()
    config = {"configurable": {"thread_id": session_id}}
    state = await agent.aget_state(config)
    msgs = state.values.get("messages", []) if state and state.values else []
    if not msgs:
        return []

    result: list[dict] = []

    for m in msgs:
        mtype = type(m).__name__
        if mtype == "HumanMessage":
            result.append({"role": "user", "content": _extract_text(m.content)})

        elif mtype == "AIMessage":
            text = _extract_text(m.content)
            tool_calls = getattr(m, "tool_calls", None) or []
            tool_call_data = [
                {
                    "name": tc["name"],
                    "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    "result": "",
                }
                for tc in tool_calls
            ]
            if tool_calls:
                # AIMessage with tool_calls — 如果同一 result 中已有 pending
                # assistant 消息（无 text 且 toolCalls 未全部填充 result），
                # 追加到它；否则新建一条 assistant 消息
                if (
                    result
                    and result[-1].get("role") == "assistant"
                    and result[-1].get("toolCalls")
                    and not result[-1]["content"]
                ):
                    # 同一 ReAct 轮次内的第二次 tool_calls，追加
                    result[-1]["toolCalls"].extend(tool_call_data)
                else:
                    result.append({
                        "role": "assistant",
                        "content": "",
                        "toolCalls": tool_call_data,
                    })
            elif text:
                # 纯文本回复 — 如果上一条是 pending assistant（有 toolCalls 无 text），
                # 把 text 填进去；否则新建
                if (
                    result
                    and result[-1].get("role") == "assistant"
                    and result[-1]["toolCalls"]
                    and not result[-1]["content"]
                ):
                    result[-1]["content"] = text
                else:
                    result.append({
                        "role": "assistant",
                        "content": text,
                        "toolCalls": [],
                    })

        elif mtype == "ToolMessage":
            result_str = str(m.content)[:500]
            # 关联到最后一个 pending toolCall（result 为空的那个）
            for r in reversed(result):
                if r.get("role") == "assistant" and r.get("toolCalls"):
                    for tc in r["toolCalls"]:
                        if not tc.get("result"):
                            tc["result"] = result_str
                            break
                    break

    return result


async def delete_session(session_id: str) -> bool:
    """删除指定 session 的所有状态数据。返回是否成功。"""
    try:
        cp = await _get_checkpointer()
        await cp.adelete_thread(session_id)
        return True
    except Exception:
        logger.exception("delete_session error for %s", session_id)
        return False
