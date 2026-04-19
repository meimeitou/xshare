"""基金基础信息"""

import json

from xshare.data.provider import get_provider, DataFetchError


async def fund_info(args: dict) -> str:
    """获取基金基础信息"""
    code = args["code"]

    try:
        info = get_provider().get_fund_basic(code)
    except DataFetchError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps(info, ensure_ascii=False, default=str)
