#!/usr/bin/env python3
"""测试 LLM 是否支持并行多工具调用 (parallel tool calls)

用法:
  # 使用 .env 中的配置
  uv run python scripts/test_parallel_tools.py

  # 指定参数
  uv run python scripts/test_parallel_tools.py --api-base https://api.example.com/v1 --api-key sk-xxx --model gpt-4
"""

import argparse
import json
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取指定城市的当前时间",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"}
                },
                "required": ["city"],
            },
        },
    },
]

PROMPT = "请同时告诉我北京的天气和上海的天气，以及北京的时间和上海的时间。"


def test_parallel_tools(api_base: str, api_key: str, model: str):
    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOLS,
        "tool_choice": "auto",
    }

    print(f"模型: {model}")
    print(f"API:  {api_base}")
    print(f"提示: {PROMPT}")
    print("-" * 60)

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"❌ 请求失败: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   响应: {e.response.text[:500]}")
        return

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    tool_calls = message.get("tool_calls", [])

    print(f"finish_reason: {choice.get('finish_reason')}")
    print(f"tool_calls 数量: {len(tool_calls)}")
    print()

    if len(tool_calls) == 0:
        print("⚠️  模型未返回 tool_calls，可能不支持工具调用")
        if message.get("content"):
            print(f"   模型回复: {message['content'][:200]}")
    elif len(tool_calls) == 1:
        tc = tool_calls[0]
        print("⚠️  模型只返回了 1 个 tool_call（不支持或不倾向并行调用）")
        print(f"   函数: {tc['function']['name']}")
        print(f"   参数: {tc['function']['arguments']}")
    else:
        print(f"✅ 模型返回了 {len(tool_calls)} 个并行 tool_calls！")
        for i, tc in enumerate(tool_calls):
            fn = tc.get("function", {})
            print(f"   [{i+1}] {fn.get('name', '?')}({fn.get('arguments', '')})")

        # 第二步：测试带 tool results 的多轮是否正常
        print()
        print("-" * 60)
        print("测试多轮：发送 tool results 后模型能否正常响应...")

        followup_messages = [
            {"role": "user", "content": PROMPT},
            message,  # assistant with tool_calls
        ]
        for tc in tool_calls:
            followup_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps({"result": f"测试数据 - {tc['function']['name']}"}),
            })

        payload2 = {
            "model": model,
            "messages": followup_messages,
            "tools": TOOLS,
        }

        try:
            resp2 = requests.post(url, headers=headers, json=payload2, timeout=30)
            resp2.raise_for_status()
            data2 = resp2.json()
            content = data2["choices"][0]["message"].get("content", "")
            print(f"✅ 多轮响应正常: {content[:200]}")
        except requests.RequestException as e:
            print(f"❌ 多轮请求失败: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"   响应: {e.response.text[:500]}")

    print()
    print("=" * 60)
    if len(tool_calls) >= 2:
        print("结论: ✅ 该模型支持并行多工具调用")
    elif len(tool_calls) == 1:
        print("结论: ⚠️  该模型可能不支持并行调用（仅返回 1 个），建议在 SOUL.md 保留单工具调用约束")
    else:
        print("结论: ❌ 该模型不支持工具调用")


def main():
    parser = argparse.ArgumentParser(description="测试 LLM 并行工具调用")
    parser.add_argument("--api-base", default=os.environ.get("DEFAULT_API_BASE", os.environ.get("CUSTOM_API_BASE", "")))
    parser.add_argument("--api-key", default=os.environ.get("DEFAULT_API_KEY", os.environ.get("CUSTOM_API_KEY", "")))
    parser.add_argument("--model", default=os.environ.get("DEFAULT_MODEL", os.environ.get("CUSTOM_MODEL", "")))
    args = parser.parse_args()

    if not all([args.api_base, args.api_key, args.model]):
        print("错误: 请提供 --api-base, --api-key, --model 或在 .env 中配置")
        sys.exit(1)

    test_parallel_tools(args.api_base, args.api_key, args.model)


if __name__ == "__main__":
    main()
