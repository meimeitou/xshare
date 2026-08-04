import pytest
from fastapi import HTTPException

from xshare.web_server import stock_list_endpoint


def _seed(conn):
    conn.execute("INSERT INTO stock_basic(code,name,market,industry,list_date) VALUES ('000001.SZ','平安银行','SZ','银行','1991-04-03')")
    conn.execute("INSERT INTO etf_basic(code,name,exchange,index_name,mgr_name,list_date) VALUES ('510300.SH','沪深300ETF','SH','沪深300','华泰柏瑞','2012-05-28')")
    conn.execute("INSERT INTO index_basic(code,name,market,publisher,category,list_date) VALUES ('000001.SH','上证指数','SSE','上交所','综合','1991-07-15')")
    conn.execute("INSERT INTO stock_daily(code,trade_date) VALUES ('000001.SZ','2026-07-24'),('000001.SZ','2026-07-25')")
    conn.execute("INSERT INTO fund_daily(code,trade_date) VALUES ('510300.SH','2026-07-23')")
    conn.execute("INSERT INTO index_daily(code,trade_date) VALUES ('000001.SH','2026-07-22')")
    # 列表端点改读 code_meta，回填种子数据
    from xshare.data.sources.tushare_source import _refresh_code_meta
    _refresh_code_meta(conn, "stock_daily", "stock", ["000001.SZ"])
    _refresh_code_meta(conn, "fund_daily", "etf", ["510300.SH"])
    _refresh_code_meta(conn, "index_daily", "index", ["000001.SH"])


@pytest.mark.asyncio
async def test_stock_list_combines_assets_and_counts(db_conn):
    _seed(db_conn)
    result = await stock_list_endpoint(q="", type=None, limit=100, offset=0)
    assert result["total"] == 3
    by_type = {item["asset_type"]: item for item in result["items"]}
    assert by_type["stock"]["industry"] == "银行"
    assert by_type["stock"]["data_count"] == 2
    assert str(by_type["stock"]["latest_trade_date"]) == "2026-07-25"
    assert by_type["etf"]["index_name"] == "沪深300"
    assert by_type["index"]["category"] == "综合"


@pytest.mark.asyncio
async def test_stock_list_filters_and_pages(db_conn):
    _seed(db_conn)
    assert (await stock_list_endpoint(q="ETF", type=None, limit=100, offset=0))["items"][0]["asset_type"] == "etf"
    result = await stock_list_endpoint(q="", type="stock", limit=1, offset=0)
    assert result["total"] == 1
    assert [item["code"] for item in result["items"]] == ["000001.SZ"]
    assert (await stock_list_endpoint(q="不存在", type=None, limit=100, offset=0))["total"] == 0


@pytest.mark.asyncio
async def test_stock_list_rejects_invalid_type(db_conn):
    with pytest.raises(HTTPException) as exc:
        await stock_list_endpoint(q="", type="fund", limit=100, offset=0)
    assert exc.value.status_code == 422
