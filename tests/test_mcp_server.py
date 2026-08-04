import json

import pytest

from xshare import mcp_server


def test_sanitize_arguments_rejects_non_object():
    args, err = mcp_server._sanitize_arguments("stock_quote", ["002594.SZ"])

    assert args == {}
    assert err is not None
    data = json.loads(err)
    assert "arguments 必须是 JSON 对象" in data["error"]
    assert data["retry_same_args"] is False


def test_sanitize_portfolio_delete_requires_id_or_code():
    args, err = mcp_server._sanitize_arguments("portfolio_update", {"action": "delete"})

    assert args["action"] == "delete"
    assert err is not None
    data = json.loads(err)
    assert "需要提供 id 或 code" in data["error"]


def test_sanitize_portfolio_delete_with_id_allows_no_code():
    args, err = mcp_server._sanitize_arguments("portfolio_update", {"action": "delete", "id": 7})

    assert err is None
    assert args["action"] == "delete"
    assert "code" not in args


def test_portfolio_update_schema_not_require_code():
    schema = mcp_server.TOOLS["portfolio_update"][1]

    assert "code" not in schema.get("required", [])


@pytest.mark.asyncio
async def test_call_tool_handles_none_arguments_without_crash():
    resp = await mcp_server.call_tool("stock_quote", None)

    assert len(resp) == 1
    data = json.loads(resp[0].text)
    assert "error" in data
    assert data["retry_same_args"] is False


@pytest.mark.asyncio
async def test_call_tool_returns_json_on_handler_exception(monkeypatch):
    async def boom(_args):
        raise RuntimeError("boom")

    monkeypatch.setitem(mcp_server.TOOLS, "__boom__", (boom, {"type": "object", "properties": {}}))

    resp = await mcp_server.call_tool("__boom__", {})

    assert len(resp) == 1
    data = json.loads(resp[0].text)
    assert "工具执行错误 [__boom__]" in data["error"]
    assert data["retry_same_args"] is False
