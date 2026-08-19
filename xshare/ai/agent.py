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


# ─── GLM 工具调用文本拦截 ─────────────────────────────────────────────────────
# GLM 模型偶尔将 tool call 以原始文本 token 输出（<|tool_calls_section|>...
# <|tool_calls_end|>），而非结构化 tool_calls 字段。LangGraph 的 ReAct 循环
# 无法识别这种文本格式的 tool call，会直接结束流，导致前端只看到一串 JSON 文本
# 然后"中断"（无 tool_result / 无实际回答）。
#
# _GLMToolCallTextInterceptor 在 messages 流式层截获这些 token：累积文本，
# 检测到完整 section 后解析为 {name, args} 并以 tool_call 事件输出，
# 而非作为 token 文本透传给前端。


class _GLMToolCallTextInterceptor:
    """累积流式文本，拦截 GLM 的文本格式工具调用。

    GLM 文本格式工具调用形如::

        <|tool_calls_section|>
        {"name": "web_search", "arguments": {"query": "...", "topic": "news"}}
        <|tool_calls_end|>

    也可能多行（多个工具调用）。拦截器在检测到 ``<|tool_calls_section|>``
    开始标记后进入缓冲模式，直到遇到 ``<|tool_calls_end|>`` 结束标记，
    将缓冲内容解析为 tool call(s)。
    """

    # GLM 文本工具调用标记有多种变体：
    #   <tool_call>...</tool_call>            （实际 API 最常见）
    #   <tool_calls_section>...</tool_calls_end>  （偶发，不含竖线）
    #   <|tool_calls_section|>...<|tool_calls_end|>  （理论格式，含竖线）
    _SECTION_STARTS = (
        "<|tool_calls_section|>",
        "<tool_calls_section>",
        "<tool_call>",
    )
    _SECTION_ENDS = (
        "<|tool_calls_end|>",
        "</tool_calls_end>",
        "</tool_call>",
    )

    def __init__(self):
        self._buffer: str = ""
        self._in_tool_section: bool = False

    def feed(self, text: str) -> tuple[list[dict], list[str]]:
        """喂入一段流式文本增量。

        Returns:
            (tool_calls, tokens)
            - tool_calls: 解析出的工具调用列表 ``[{"name","args"}, ...]``，
              仅在该段触发了完整 section 结束时非空。
            - tokens: 应作为普通 token 输出的文本片段列表（section 外的文本）。
        """
        tool_calls: list[dict] = []
        tokens: list[str] = []
        self._buffer += text

        while self._buffer:
            if not self._in_tool_section:
                # 查找任一 section 开始标记
                idx, marker, mlen = self._find_earliest(self._buffer, self._SECTION_STARTS)
                if idx == -1:
                    # 没有标记 — 但尾部可能是部分标记前缀，需要保留
                    # 保留最后 max(len(marker))-1 个字符防止截断
                    max_marker_len = max(len(m) for m in self._SECTION_STARTS)
                    safe = len(self._buffer) - (max_marker_len - 1)
                    if safe > 0:
                        tokens.append(self._buffer[:safe])
                        self._buffer = self._buffer[safe:]
                    break
                else:
                    # 标记前的文本是普通 token
                    if idx > 0:
                        tokens.append(self._buffer[:idx])
                    self._buffer = self._buffer[idx + mlen:]
                    self._in_tool_section = True
            else:
                # 在 section 内 — 查找任一结束标记
                idx, marker, mlen = self._find_earliest(self._buffer, self._SECTION_ENDS)
                if idx == -1:
                    # 还没结束，继续缓冲
                    break
                else:
                    section_body = self._buffer[:idx]
                    self._buffer = self._buffer[idx + mlen:]
                    self._in_tool_section = False
                    parsed = self._parse_section(section_body)
                    tool_calls.extend(parsed)

        return tool_calls, tokens

    def flush(self) -> tuple[list[dict], list[str]]:
        """流结束时调用，返回残留缓冲。

        如果结束时仍在 section 内（不完整），将原始文本作为 token 返回。
        """
        if self._in_tool_section:
            # 不完整的 section — 作为普通文本返回，避免吞掉用户可见内容
            text = self._buffer
            self._buffer = ""
            self._in_tool_section = False
            return [], [text]
        text = self._buffer
        self._buffer = ""
        return [], [text] if text else []

    @staticmethod
    def _find_earliest(text: str, markers: tuple[str, ...]) -> tuple[int, str, int]:
        """在 text 中查找最早出现的标记。返回 (index, marker, marker_len)。
        未找到时返回 (-1, "", 0)。"""
        best_idx = -1
        best_marker = ""
        for m in markers:
            idx = text.find(m)
            if idx == -1:
                continue
            if best_idx == -1 or idx < best_idx:
                best_idx = idx
                best_marker = m
        return best_idx, best_marker, len(best_marker)

    @staticmethod
    def _parse_section(body: str) -> list[dict]:
        """解析 section body 文本为 tool call dict 列表。

        body 可能包含多行 JSON，每行一个工具调用::
            {"name": "web_search", "arguments": {...}}
            {"name": "stock_quote", "arguments": {...}}
        """
        results: list[dict] = []
        for line in body.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # 尝试提取第一个 JSON 对象
                start = line.find("{")
                end = line.rfind("}")
                if start != -1 and end > start:
                    try:
                        obj = json.loads(line[start : end + 1])
                    except json.JSONDecodeError:
                        logger.warning("GLM tool section 解析失败: %s", line[:100])
                        continue
                else:
                    continue
            name = obj.get("name", "")
            args = obj.get("arguments") or obj.get("args") or {}
            if name:
                results.append({"name": name, "args": args})
        return results

# ─── SSE 流式输出 ──────────────────────────────────────────────────────────────
async def chat_stream(session_id: str, user_message: str):
    """async generator，yield {"data": json_str} dict。

    流式映射:
    - updates 模式: tool_call / tool_result 事件
    - messages 模式: token 级文本增量
    - 流结束: done 事件

    GLM 文本工具调用恢复: 当 GLM 将 tool call 以文本格式输出时（经拦截器检测），
    手动执行工具并将结果注入 agent 状态，然后重新驱动 agent 继续推理。
    """
    agent = await get_agent()
    config = {"configurable": {"thread_id": session_id}}
    tools_by_name = {t.name: t for t in build_tools()}
    max_recovery_rounds = 5  # ponytail: 防止 GLM 反复输出文本 tool call 死循环

    # 首轮：用户消息驱动 agent
    stream_input = {"messages": [HumanMessage(content=user_message)]}

    for _recovery_round in range(max_recovery_rounds + 1):
        _interceptor = _GLMToolCallTextInterceptor()
        _round_text_tcs: list[dict] = []  # 本轮检测到的所有文本工具调用

        try:
            async for chunk in agent.astream(
                stream_input,
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
                    if not text:
                        continue
                    # GLM 偶尔将 tool call 以文本格式输出（<tool_call>...</tool_call>）
                    # 而非结构化 tool_calls，经拦截器解析为 tool_call 事件
                    tool_calls, tokens = _interceptor.feed(text)
                    if tool_calls:
                        logger.info("拦截器检测到文本工具调用: %s", [tc["name"] for tc in tool_calls])
                        _round_text_tcs.extend(tool_calls)
                    for tc in tool_calls:
                        yield {"data": json.dumps(
                            {
                                "type": "tool_call",
                                "name": tc["name"],
                                "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                            },
                            ensure_ascii=False,
                        )}
                    for tok in tokens:
                        yield {"data": json.dumps(
                            {"type": "token", "content": tok},
                            ensure_ascii=False,
                        )}

        except Exception as e:
            logger.exception("chat_stream error")
            yield {"data": json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)}
            return

        # 流结束 — 刷出拦截器残留缓冲
        tc_flush, tok_flush = _interceptor.flush()
        _round_text_tcs.extend(tc_flush)
        for tc in tc_flush:
            yield {"data": json.dumps(
                {
                    "type": "tool_call",
                    "name": tc["name"],
                    "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                },
                ensure_ascii=False,
            )}
        for tok in tok_flush:
            yield {"data": json.dumps(
                {"type": "token", "content": tok},
                ensure_ascii=False,
            )}

        # 检查是否有 GLM 文本格式工具调用需要恢复
        text_tool_calls = _round_text_tcs
        if not text_tool_calls:
            break  # 无文本工具调用 — 正常结束

        if _recovery_round >= max_recovery_rounds:
            logger.warning("GLM 文本工具调用恢复已达最大轮次 %d，停止", max_recovery_rounds)
            yield {"data": json.dumps(
                {"type": "token", "content": "\n\n[达到最大工具调用轮次，已停止]"},
                ensure_ascii=False,
            )}
            break

        # 手动执行工具并将结果注入 agent 状态
        from langchain_core.messages import AIMessage, ToolMessage

        # 构造 AIMessage（含 tool_calls）注入状态，让 ReAct 循环能关联 ToolMessage
        structured_tcs = []
        for i, tc in enumerate(text_tool_calls):
            tc_id = f"text_tc_{_recovery_round}_{i}"
            structured_tcs.append({
                "name": tc["name"],
                "args": tc.get("args", {}),
                "id": tc_id,
                "type": "tool_call",
            })

        # 获取 agent 当前状态的最后一条消息
        state = await agent.aget_state(config)
        msgs = state.values.get("messages", [])
        # 最后一条消息是 GLM 输出的纯文本（含 tool_calls_section 文本）
        # 用结构化 tool_calls 替换它，让 ReAct 循环正确关联 ToolMessage
        if msgs:
            last_msg = msgs[-1]
            # 替换最后一条 AIMessage 为带结构化 tool_calls 的版本
            new_ai_msg = AIMessage(
                content=last_msg.content if not isinstance(last_msg.content, str) else "",
                tool_calls=structured_tcs,
            )
            # 用 aupdate_state 注入：替换最后一条消息
            await agent.aupdate_state(
                config,
                {"messages": [new_ai_msg]},
                as_node="agent",
            )

        # 执行每个工具，收集 ToolMessage 结果
        tool_messages = []
        for tc_struct in structured_tcs:
            tool_name = tc_struct["name"]
            tool_args = tc_struct["args"]
            tc_id = tc_struct["id"]
            logger.info("恢复执行文本工具调用: %s(%s)", tool_name, tool_args)
            try:
                lc_tool = tools_by_name.get(tool_name)
                if lc_tool is None:
                    result = json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
                else:
                    result = await lc_tool.ainvoke(tool_args)
            except Exception as e:
                logger.exception("恢复执行工具 %s 失败", tool_name)
                result = json.dumps({"error": str(e)}, ensure_ascii=False)

            yield {"data": json.dumps(
                {
                    "type": "tool_result",
                    "name": tool_name,
                    "result": str(result)[:500],
                },
                ensure_ascii=False,
            )}
            tool_messages.append(ToolMessage(
                content=str(result),
                tool_call_id=tc_id,
                name=tool_name,
            ))

        # 注入 ToolMessage 结果，让 agent 继续推理
        await agent.aupdate_state(
            config,
            {"messages": tool_messages},
            as_node="tools",
        )

        # 下一轮：None 输入 = 从 checkpoint 继续
        stream_input = None

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
