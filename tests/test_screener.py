import json

import pytest

from xshare.tools import screener


def _seed_screen_data(conn):
    """插入股票基础信息 + 财务数据。"""
    conn.execute("INSERT INTO stock_basic (code, name, industry) VALUES (?, ?, ?)",
                 ["002594.SZ", "比亚迪", "汽车"])
    conn.execute("INSERT INTO stock_basic (code, name, industry) VALUES (?, ?, ?)",
                 ["300750.SZ", "宁德时代", "电池"])
    conn.execute("INSERT INTO stock_basic (code, name, industry) VALUES (?, ?, ?)",
                 ["600519.SH", "贵州茅台", "白酒"])

    rows = [
        ("002594.SZ", "2024-12-31", 25.0, 3.5, 15.0, 1.5e10, 1.5e9, 20.0, 25.0),
        ("300750.SZ", "2024-12-31", 30.0, 4.0, 12.0, 2.0e10, 2.0e9, 18.0, 20.0),
        ("600519.SH", "2024-12-31", 40.0, 8.0, 25.0, 1.0e10, 5.0e9, 10.0, 12.0),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO stock_finance (code, end_date, pe, pb, roe, revenue, net_profit, revenue_yoy, profit_yoy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(r),
        )


@pytest.mark.asyncio
async def test_stock_screen_by_pe(db_conn):
    _seed_screen_data(db_conn)

    resp = await screener.stock_screen({"filters": [{"field": "pe", "op": "<", "value": 35}]})
    data = json.loads(resp)

    assert data["count"] == 2
    codes = {r["code"] for r in data["results"]}
    assert "002594.SZ" in codes
    assert "300750.SZ" in codes
    assert "600519.SH" not in codes


@pytest.mark.asyncio
async def test_stock_screen_with_sector(db_conn):
    _seed_screen_data(db_conn)

    resp = await screener.stock_screen({
        "filters": [{"field": "roe", "op": ">", "value": 10}],
        "sector": "汽车",
    })
    data = json.loads(resp)

    assert data["count"] == 1
    assert data["results"][0]["code"] == "002594.SZ"
    assert data["results"][0]["industry"] == "汽车"


@pytest.mark.asyncio
async def test_stock_screen_limit(db_conn):
    _seed_screen_data(db_conn)

    resp = await screener.stock_screen({
        "filters": [{"field": "pe", "op": ">", "value": 0}],
        "limit": 2,
    })
    data = json.loads(resp)

    assert data["count"] == 2


@pytest.mark.asyncio
async def test_stock_screen_invalid_field(db_conn):
    resp = await screener.stock_screen({
        "filters": [{"field": "market_cap", "op": ">", "value": 100}],
    })
    data = json.loads(resp)

    assert "error" in data
    assert "不支持的筛选字段" in data["error"]


@pytest.mark.asyncio
async def test_stock_screen_invalid_op(db_conn):
    resp = await screener.stock_screen({
        "filters": [{"field": "pe", "op": "LIKE", "value": 25}],
    })
    data = json.loads(resp)

    assert "error" in data
    assert "不支持的操作符" in data["error"]


@pytest.mark.asyncio
async def test_stock_screen_no_results(db_conn):
    _seed_screen_data(db_conn)

    resp = await screener.stock_screen({
        "filters": [{"field": "pe", "op": ">", "value": 999}],
    })
    data = json.loads(resp)

    assert data["count"] == 0
    assert data["results"] == []
