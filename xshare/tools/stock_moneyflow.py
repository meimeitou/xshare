"""个股资金流向查询 — 读取本地 stock_moneyflow 表，返回四档净额 + 背离标签。"""

import json

from xshare.data.db import get_conn
from xshare.utils import to_json_safe


def _divergence_label(main_force: float, retail: float) -> str:
    """主力(elg+lg) vs 散户(sm+md) 净额方向 → 背离标签。"""
    if main_force > 0 and retail < 0:
        return "主力吸筹·散户割肉"
    if main_force < 0 and retail > 0:
        return "主力派发·散户接盘"
    if main_force > 0 and retail > 0:
        return "合力净买入"
    if main_force < 0 and retail < 0:
        return "合力净卖出"
    return "资金均衡"


async def stock_moneyflow(args: dict) -> str:
    """查询个股最近 N 个交易日的资金流向（四档净额 + 背离标签）。

    数据来源：本地 DuckDB ``stock_moneyflow`` 表（Tushare moneyflow 同步），
    金额单位万元。不调用外部 API。
    """
    code = args.get("code")
    if not code:
        return json.dumps(
            {"error": "缺少股票代码", "retry_same_args": False},
            ensure_ascii=False,
        )

    days = int(args.get("days", 10))
    days = max(1, min(days, 60))

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            trade_date,
            COALESCE(buy_sm_amount, 0)  - COALESCE(sell_sm_amount, 0)  AS sm_net,
            COALESCE(buy_md_amount, 0)  - COALESCE(sell_md_amount, 0)  AS md_net,
            COALESCE(buy_lg_amount, 0)  - COALESCE(sell_lg_amount, 0)  AS lg_net,
            COALESCE(buy_elg_amount, 0) - COALESCE(sell_elg_amount, 0) AS elg_net,
            COALESCE(net_mf_amount, 0) AS net_mf_amount
        FROM stock_moneyflow
        WHERE code = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        [code, days],
    ).fetchall()

    if not rows:
        return json.dumps(
            {"error": f"未找到 {code} 的资金流向数据", "retry_same_args": False},
            ensure_ascii=False,
        )

    daily = []
    for r in rows:
        sm = float(r[1])
        md = float(r[2])
        lg = float(r[3])
        elg = float(r[4])
        total = float(r[5])
        main_force = elg + lg
        retail = sm + md
        daily.append({
            "trade_date": str(r[0]),
            "sm_net_amount": round(sm, 2),
            "md_net_amount": round(md, 2),
            "lg_net_amount": round(lg, 2),
            "elg_net_amount": round(elg, 2),
            "net_mf_amount": round(total, 2),
            "main_force_net": round(main_force, 2),
            "retail_net": round(retail, 2),
            "divergence": _divergence_label(main_force, retail),
        })

    # 汇总：最近 N 日合计
    sm_sum = sum(d["sm_net_amount"] for d in daily)
    md_sum = sum(d["md_net_amount"] for d in daily)
    lg_sum = sum(d["lg_net_amount"] for d in daily)
    elg_sum = sum(d["elg_net_amount"] for d in daily)
    total_sum = sum(d["net_mf_amount"] for d in daily)
    mf_sum = elg_sum + lg_sum
    ret_sum = sm_sum + md_sum

    summary = {
        "sm_net_amount": round(sm_sum, 2),
        "md_net_amount": round(md_sum, 2),
        "lg_net_amount": round(lg_sum, 2),
        "elg_net_amount": round(elg_sum, 2),
        "net_mf_amount": round(total_sum, 2),
        "main_force_net": round(mf_sum, 2),
        "retail_net": round(ret_sum, 2),
        "divergence": _divergence_label(mf_sum, ret_sum),
        "days": len(daily),
    }

    result = {
        "code": code,
        "daily": daily,
        "summary": summary,
        "latest_date": daily[0]["trade_date"],
        "source": "stock_moneyflow",
    }
    return json.dumps(to_json_safe(result), ensure_ascii=False, allow_nan=False)
