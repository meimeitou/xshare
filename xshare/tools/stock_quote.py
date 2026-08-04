"""实时行情"""

import json

from xshare.data.provider import DataFetchError, get_provider


async def stock_quote(args: dict) -> str:
    """获取股票实时行情"""
    code = args.get("code", "")
    if not code:
        return json.dumps({
            "error": "缺少股票代码",
            "retry_same_args": False,
        }, ensure_ascii=False)

    try:
        provider = get_provider()
        quote = provider.get_realtime_quote(code)
        return json.dumps(quote.to_dict(), ensure_ascii=False, default=str)
    except DataFetchError as e:
        return json.dumps({
            "error": str(e),
            "code": code,
            "retry_same_args": False,
            "hint": "实时行情暂不可用，可稍后重试或结合本地日线分析",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{code} 行情获取失败：{e}",
            "code": code,
            "retry_same_args": False,
        }, ensure_ascii=False)
