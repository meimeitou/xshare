from datetime import date

import duckdb
import pandas as pd

from xshare.data.provider import ProviderManager


def test_upsert_finance_full_fields_with_current_schema():
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE stock_finance (
            code VARCHAR NOT NULL,
            end_date DATE NOT NULL,
            pe DOUBLE,
            pb DOUBLE,
            roe DOUBLE,
            revenue DOUBLE,
            net_profit DOUBLE,
            revenue_yoy DOUBLE,
            profit_yoy DOUBLE,
            updated_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (code, end_date)
        )
        """
    )

    df = pd.DataFrame(
        [
            {
                "code": "002594.SZ",
                "end_date": date(2026, 3, 31),
                "pe": 22.1,
                "pb": 3.8,
                "roe": 12.3,
                "revenue": 1000.0,
                "net_profit": 120.0,
                "revenue_yoy": 15.0,
                "profit_yoy": 18.0,
            },
            {
                "code": "002594.SZ",
                "end_date": date(2026, 3, 31),
                "pe": 21.9,
                "pb": 3.7,
                "roe": 13.4,
                "revenue": 1010.0,
                "net_profit": 130.0,
                "revenue_yoy": 16.0,
                "profit_yoy": 19.0,
            },
        ]
    )

    ProviderManager._upsert_finance(conn, df)

    row = conn.execute(
        "SELECT code, end_date, pe, pb, roe, revenue, net_profit, revenue_yoy, profit_yoy "
        "FROM stock_finance WHERE code='002594.SZ'"
    ).fetchone()
    assert row is not None
    assert row[0] == "002594.SZ"
    assert row[2] == 21.9
    assert row[3] == 3.7
    assert row[4] == 13.4
    assert row[5] == 1010.0
    assert row[6] == 130.0
    assert row[7] == 16.0
    assert row[8] == 19.0
