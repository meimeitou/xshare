import json
from datetime import datetime, timedelta

import pytest

from xshare.data.sources import ths_news
from xshare.tools import stock_news


def _seed_news(conn, count=3):
    now = datetime.now()
    for i in range(count):
        conn.execute(
            "INSERT INTO news (id, publish_time, source, title, content, stock_codes, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                f"news-{i}",
                now - timedelta(hours=i),
                "同花顺" if i % 2 == 0 else "东财",
                f"比亚迪发布第{i}季度财报",
                f"本季度营收同比增长20%，内容详情{i}...",
                ["002594.SZ"],
                ["财报", "业绩"],
            ],
        )


@pytest.mark.asyncio
async def test_stock_news_by_code(db_conn):
    _seed_news(db_conn, count=3)

    resp = await stock_news.stock_news({"code": "002594.SZ", "days": 7})
    data = json.loads(resp)

    assert len(data["news"]) == 3
    assert "title" in data["news"][0]
    assert "time" in data["news"][0]
    assert "source" in data["news"][0]
    assert "summary" in data["news"][0]


@pytest.mark.asyncio
async def test_stock_news_by_keyword(db_conn):
    _seed_news(db_conn, count=3)

    resp = await stock_news.stock_news({"keyword": "财报", "days": 7})
    data = json.loads(resp)

    assert len(data["news"]) == 3


@pytest.mark.asyncio
async def test_stock_news_empty(db_conn):
    resp = await stock_news.stock_news({"code": "002594.SZ"})
    data = json.loads(resp)

    assert data["news"] == []
    assert "未找到" in data["message"]


@pytest.mark.asyncio
async def test_stock_news_code_suffix_compatible(db_conn):
    """入库为 6 位裸码时，查询带交易所后缀也应命中。"""
    db_conn.execute(
        "INSERT INTO news (id, publish_time, source, title, content, stock_codes, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["bare-code-1", datetime.now(), "同花顺", "比亚迪公告", "公告内容", ["002594"], []],
    )

    resp = await stock_news.stock_news({"code": "002594.SZ", "days": 7})
    data = json.loads(resp)

    assert len(data["news"]) == 1
    assert "比亚迪" in data["news"][0]["title"]


def test_fetch_all_pages_continue_after_empty_page(monkeypatch):
    """单页为空不应中断后续分页抓取。"""
    page_map = {
        1: [{"id": "n1"}],
        2: [],
        3: [{"id": "n3"}],
    }

    def fake_fetch_realtime_news(page: int = 1, pagesize: int = 50):
        return page_map.get(page, [])

    monkeypatch.setattr(ths_news, "fetch_realtime_news", fake_fetch_realtime_news)
    monkeypatch.setattr(ths_news.time, "sleep", lambda _: None)

    rows = ths_news.fetch_all_pages(max_pages=3, delay=0)
    assert [r["id"] for r in rows] == ["n1", "n3"]


@pytest.mark.asyncio
async def test_stock_news_summary_truncation(db_conn):
    """summary 不超过 200 字。"""
    long_content = "详情" * 200  # 400 字
    db_conn.execute(
        "INSERT INTO news (id, publish_time, source, title, content, stock_codes, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["long-1", datetime.now(), "测试", "长内容新闻", long_content, [], []],
    )

    resp = await stock_news.stock_news({"days": 1})
    data = json.loads(resp)

    assert len(data["news"]) == 1
    assert len(data["news"][0]["summary"]) <= 200
