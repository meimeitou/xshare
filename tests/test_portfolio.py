import json
from datetime import date

import pytest

from xshare.tools import portfolio


def _seed_stock_basic(conn):
    conn.execute(
        "INSERT INTO stock_basic (code, name, industry) VALUES (?, ?, ?)",
        ["002594.SZ", "比亚迪", "汽车"],
    )


@pytest.mark.asyncio
async def test_portfolio_buy(db_conn):
    _seed_stock_basic(db_conn)

    resp = await portfolio.portfolio_update({
        "action": "buy",
        "code": "002594.SZ",
        "price": 100.0,
        "quantity": 200,
    })
    data = json.loads(resp)

    assert data["success"] is True
    assert "买入" in data["message"]
    assert data["record"]["id"] > 0
    assert data["record"]["code"] == "002594.SZ"
    assert data["record"]["name"] == "比亚迪"
    assert data["record"]["direction"] == "buy"
    assert data["record"]["quantity"] == 200
    assert data["record"]["amount"] == 20000.0


@pytest.mark.asyncio
async def test_portfolio_buy_auto_name(db_conn):
    """未提供 name 时从 stock_basic 自动补全。"""
    _seed_stock_basic(db_conn)

    resp = await portfolio.portfolio_update({
        "code": "002594.SZ",
        "price": 100.0,
        "quantity": 100,
    })
    data = json.loads(resp)

    assert data["success"] is True
    assert data["record"]["name"] == "比亚迪"


@pytest.mark.asyncio
async def test_portfolio_sell_success(db_conn):
    _seed_stock_basic(db_conn)

    await portfolio.portfolio_update({
        "code": "002594.SZ", "price": 100.0, "quantity": 200,
    })
    resp = await portfolio.portfolio_update({
        "action": "sell",
        "code": "002594.SZ",
        "price": 110.0,
        "quantity": 100,
    })
    data = json.loads(resp)

    assert data["success"] is True
    assert "卖出" in data["message"]
    assert data["record"]["direction"] == "sell"
    assert data["record"]["quantity"] == -100


@pytest.mark.asyncio
async def test_portfolio_sell_exceeds_holding(db_conn):
    _seed_stock_basic(db_conn)

    await portfolio.portfolio_update({
        "code": "002594.SZ", "price": 100.0, "quantity": 50,
    })
    resp = await portfolio.portfolio_update({
        "action": "sell",
        "code": "002594.SZ",
        "price": 110.0,
        "quantity": 100,
    })
    data = json.loads(resp)

    assert data["success"] is False
    assert "超过当前持仓" in data["message"]


@pytest.mark.asyncio
async def test_portfolio_delete_by_code(db_conn):
    _seed_stock_basic(db_conn)

    await portfolio.portfolio_update({
        "code": "002594.SZ", "price": 100.0, "quantity": 100,
    })
    resp = await portfolio.portfolio_update({
        "action": "delete",
        "code": "002594.SZ",
    })
    data = json.loads(resp)

    assert data["success"] is True
    assert "已删除" in data["message"]


@pytest.mark.asyncio
async def test_portfolio_delete_by_id_without_code(db_conn):
    """Web DELETE /api/portfolio/{id} 只传 id，不应要求 code。"""
    _seed_stock_basic(db_conn)

    await portfolio.portfolio_update({
        "code": "002594.SZ", "price": 100.0, "quantity": 100, "name": "比亚迪",
    })
    from xshare.data.sqlite_db import get_sqlite_conn
    record_id = get_sqlite_conn().execute("SELECT id FROM portfolio").fetchone()[0]

    resp = await portfolio.portfolio_update({"action": "delete", "id": record_id})
    data = json.loads(resp)

    assert data["success"] is True
    assert str(record_id) in data["message"]
    assert get_sqlite_conn().execute("SELECT COUNT(*) FROM portfolio").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_portfolio_summary_empty(db_conn):
    resp = await portfolio.portfolio_summary({})
    data = json.loads(resp)

    assert data["holdings"] == []
    assert data["records"] == []
    assert "无交易记录" in data["message"]


@pytest.mark.asyncio
async def test_portfolio_summary_with_holdings(db_conn):
    _seed_stock_basic(db_conn)
    db_conn.execute(
        "INSERT INTO stock_basic (code, name) VALUES (?, ?)",
        ["300750.SZ", "宁德时代"],
    )

    await portfolio.portfolio_update({
        "code": "002594.SZ", "price": 100.0, "quantity": 200,
    })
    await portfolio.portfolio_update({
        "code": "300750.SZ", "price": 200.0, "quantity": 100,
    })
    await portfolio.portfolio_update({
        "action": "sell", "code": "002594.SZ", "price": 120.0, "quantity": 100,
    })

    resp = await portfolio.portfolio_summary({})
    data = json.loads(resp)

    assert data["total_positions"] == 2
    assert data["total_holding_cost"] > 0
    assert data["total_cost"] == data["total_holding_cost"]
    # 已实现盈亏 = 卖出金额(120*100) - 卖出股数(100) * 买入均价(100) = 2000
    assert data["realized_pnl"] == 2000.0
    assert len(data["cleared"]) == 0  # 002594 仍有 100 股未清仓
    assert len(data["records"]) == 3
    assert all("id" in r for r in data["records"])

    codes = {h["code"] for h in data["holdings"]}
    assert "002594.SZ" in codes
    assert "300750.SZ" in codes


@pytest.mark.asyncio
async def test_portfolio_delete_missing_id(db_conn):
    resp = await portfolio.portfolio_update({"action": "delete", "id": 999999})
    data = json.loads(resp)
    assert data["success"] is False
    assert "未找到" in data["message"]


@pytest.mark.asyncio
async def test_portfolio_summary_cleared_position(db_conn):
    _seed_stock_basic(db_conn)

    await portfolio.portfolio_update({
        "code": "002594.SZ", "price": 100.0, "quantity": 100,
    })
    await portfolio.portfolio_update({
        "action": "sell", "code": "002594.SZ", "price": 110.0, "quantity": 100,
    })

    resp = await portfolio.portfolio_summary({})
    data = json.loads(resp)

    assert data["total_positions"] == 0
    assert data["realized_pnl"] == 1000.0
    assert len(data["cleared"]) == 1
    assert data["cleared"][0]["code"] == "002594.SZ"
