"""实时行情"""

import json

from xshare.data.provider import get_provider


async def stock_quote(args: dict) -> str:
    """获取股票实时行情"""
    code = args["code"]
    quote = get_provider().get_realtime_quote(code)
    return json.dumps(quote.to_dict(), ensure_ascii=False)
