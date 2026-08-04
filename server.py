"""FastMCP wrapper for local MCP Inspector testing.

xshare/mcp_server.py uses the low-level mcp.server.Server API which `mcp dev`
does not support. This wrapper re-exports the same tools via FastMCP, preserving
exact input schemas, so `uv run mcp dev server.py` works for interactive testing.

Production entry point remains xshare/mcp_server.py
(``uv run xshare serve`` or ``uv run python -m xshare.mcp_server``).
"""

import inspect

from mcp.server.fastmcp import FastMCP

from xshare.mcp_server import (
    TOOLS,
    _sanitize_arguments,
    _get_tool_description,
)

mcp = FastMCP("xshare")

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_wrapper(tool_name, handler, schema):
    """Build an async wrapper with a signature FastMCP can introspect."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    params = []
    for prop_name, prop_schema in properties.items():
        py_type = _JSON_TYPE_MAP.get(prop_schema.get("type", "string"), str)
        if prop_name in required:
            params.append(
                inspect.Parameter(
                    prop_name, inspect.Parameter.KEYWORD_ONLY, annotation=py_type
                )
            )
        else:
            default = prop_schema.get("default")
            params.append(
                inspect.Parameter(
                    prop_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=py_type,
                    default=default,
                )
            )

    async def wrapper(**kwargs):
        args, err = _sanitize_arguments(tool_name, kwargs)
        if err:
            return err
        return await handler(args)

    wrapper.__signature__ = inspect.Signature(params)
    wrapper.__name__ = tool_name
    return wrapper


for _name, (_handler, _schema) in TOOLS.items():
    mcp.add_tool(
        _build_wrapper(_name, _handler, _schema),
        name=_name,
        description=_get_tool_description(_name),
    )
    mcp._tool_manager._tools[_name].parameters = _schema


if __name__ == "__main__":
    import asyncio
    from xshare.data.db import init_tables
    from xshare.data.sqlite_db import init_sqlite_tables
    from xshare.data.sync_config import init_sync_config

    init_tables()
    init_sqlite_tables()
    init_sync_config()
    mcp.run()
