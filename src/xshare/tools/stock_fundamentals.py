"""基本面数据"""

import json

from xshare.data.provider import get_provider, DataFetchError


async def stock_fundamentals(args: dict) -> str:
    """获取股票基本面数据"""
    code = args["code"]

    try:
        df = get_provider().get_financial_data(code)
    except DataFetchError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    if df.empty:
        return json.dumps({"error": f"未找到 {code} 的财务数据"}, ensure_ascii=False)

    row = df.sort_values("end_date", ascending=False).iloc[0]
    data = row.to_dict()
    for k, v in data.items():
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()

    return json.dumps(data, ensure_ascii=False)
