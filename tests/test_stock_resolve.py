import json
from datetime import date

import pytest

from xshare.tools import stock_resolve


def _seed_stock_basic(conn):
    rows = [
        ("002594.SZ", "比亚迪", "SZ", "汽车", date(2011, 6, 30)),
        ("002594.HK", "比亚迪股份", "HK", "汽车", date(2011, 6, 30)),
        ("300750.SZ", "宁德时代", "SZ", "电池", date(2018, 6, 11)),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO stock_basic (code, name, market, industry, list_date) VALUES (?, ?, ?, ?, ?)",
            list(r),
        )


@pytest.mark.asyncio
async def test_stock_resolve_local_match(db_conn, monkeypatch):
    _seed_stock_basic(db_conn)

    resp = await stock_resolve.stock_resolve({"query": "比亚迪"})
    data = json.loads(resp)

    assert len(data["matches"]) >= 2
    names = {m["name"] for m in data["matches"]}
    assert "比亚迪" in names


@pytest.mark.asyncio
async def test_stock_resolve_by_code(db_conn, monkeypatch):
    _seed_stock_basic(db_conn)

    resp = await stock_resolve.stock_resolve({"query": "002594"})
    data = json.loads(resp)

    assert len(data["matches"]) >= 1
    assert data["matches"][0]["code"].startswith("002594")


@pytest.mark.asyncio
async def test_stock_resolve_no_match(db_conn, monkeypatch):
    """无 TUSHARE_TOKEN 且本地无匹配时返回空列表。"""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    resp = await stock_resolve.stock_resolve({"query": "不存在的股票XYZ"})
    data = json.loads(resp)

    assert data["matches"] == []
    assert "未找到" in data["message"]


@pytest.mark.asyncio
async def test_stock_resolve_limit(db_conn, monkeypatch):
    """结果限制在 10 条以内。"""
    for i in range(15):
        db_conn.execute(
            "INSERT INTO stock_basic (code, name) VALUES (?, ?)",
            [f"{i:06d}.SZ", f"测试股票{i}"],
        )

    resp = await stock_resolve.stock_resolve({"query": "测试"})
    data = json.loads(resp)

    assert len(data["matches"]) <= 10


@pytest.mark.asyncio
async def test_stock_resolve_by_full_pinyin(db_conn, monkeypatch):
    _seed_stock_basic(db_conn)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    resp = await stock_resolve.stock_resolve({"query": "biyadi"})
    data = json.loads(resp)

    assert len(data["matches"]) >= 1
    assert any(item["name"].startswith("比亚迪") for item in data["matches"])


@pytest.mark.asyncio
async def test_stock_resolve_by_initials(db_conn, monkeypatch):
    _seed_stock_basic(db_conn)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    resp = await stock_resolve.stock_resolve({"query": "byd"})
    data = json.loads(resp)

    assert len(data["matches"]) >= 1
    assert any(item["name"].startswith("比亚迪") for item in data["matches"])


@pytest.mark.asyncio
async def test_stock_resolve_ranking_code_prefix_first(db_conn, monkeypatch):
    _seed_stock_basic(db_conn)
    db_conn.execute(
        "INSERT INTO stock_basic (code, name) VALUES (?, ?)",
        ["900001.SZ", "测试002594概念"],
    )
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    resp = await stock_resolve.stock_resolve({"query": "002594"})
    data = json.loads(resp)

    assert data["matches"]
    assert data["matches"][0]["code"].startswith("002594")
