"""数据集水位（sync_watermark）读写与缺口扫描。

日线以 trade_date（YYYY-MM-DD）为 key；全量类数据集用 key='ALL'。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from xshare.data.db import get_conn, init_tables

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"
STATUS_PENDING = "pending"

DATASET_DAILY = "daily"
DATASET_STOCK_BASIC = "stock_basic"
DATASET_INDEX_BASIC = "index_basic"
DATASET_INDEX_DAILY = "index_daily"
DATASET_ETF_BASIC = "etf_basic"
DATASET_FUND_DAILY = "fund_daily"
DATASET_TRADE_CAL = "trade_cal"
DATASET_DAILY_BASIC = "daily_basic"
DATASET_FINANCE = "finance"
DATASET_MONEYFLOW = "moneyflow"
DATASET_SECTOR_MONEYFLOW = "sector_moneyflow"
DATASET_MARKET_MONEYFLOW = "market_moneyflow"
DATASET_LIMIT_LIST = "limit_list"
DATASET_CONCEPT_BOARD = "concept_board"
DATASET_CONCEPT_MEMBER = "concept_member"
DATASET_NEWS = "news"

# key 语义：date=YYYY-MM-DD，all=全量快照（key='ALL'），code=股票/ETF 代码
KEY_TYPE_DATE = "date"
KEY_TYPE_ALL = "all"
KEY_TYPE_CODE = "code"

_KEY_TYPE_MAP: dict[str, str] = {
    DATASET_DAILY: KEY_TYPE_DATE,
    DATASET_INDEX_DAILY: KEY_TYPE_DATE,
    DATASET_FUND_DAILY: KEY_TYPE_DATE,
    DATASET_DAILY_BASIC: KEY_TYPE_DATE,
    DATASET_STOCK_BASIC: KEY_TYPE_ALL,
    DATASET_INDEX_BASIC: KEY_TYPE_ALL,
    DATASET_ETF_BASIC: KEY_TYPE_ALL,
    DATASET_TRADE_CAL: KEY_TYPE_ALL,
    DATASET_NEWS: KEY_TYPE_ALL,
    DATASET_FINANCE: KEY_TYPE_CODE,
    DATASET_MONEYFLOW: KEY_TYPE_DATE,
    DATASET_SECTOR_MONEYFLOW: KEY_TYPE_DATE,
    DATASET_MARKET_MONEYFLOW: KEY_TYPE_DATE,
    DATASET_LIMIT_LIST: KEY_TYPE_DATE,
    DATASET_CONCEPT_MEMBER: KEY_TYPE_DATE,
}


def get_key_type(dataset: str) -> str | None:
    """返回该数据集的 key 语义类型，未知返回 None。"""
    return _KEY_TYPE_MAP.get(dataset)


def _key_str(key: str | date | datetime) -> str:
    if isinstance(key, datetime):
        return key.date().isoformat()
    if isinstance(key, date):
        return key.isoformat()
    return str(key)


def set_watermark(
    dataset: str,
    key: str | date | datetime,
    status: str,
    row_count: int | None = None,
    error: str | None = None,
) -> None:
    """写入或更新水位。status=ok 时刷新 last_success_at。"""
    init_tables()
    conn = get_conn()
    k = _key_str(key)
    kt = get_key_type(dataset)
    now = datetime.now()
    if status == STATUS_OK:
        conn.execute(
            """
            INSERT INTO sync_watermark (dataset, key, key_type, status, row_count, last_success_at, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT (dataset, key) DO UPDATE SET
                status=EXCLUDED.status,
                row_count=EXCLUDED.row_count,
                last_success_at=EXCLUDED.last_success_at,
                last_error=NULL,
                key_type=COALESCE(EXCLUDED.key_type, sync_watermark.key_type),
                updated_at=EXCLUDED.updated_at
            """,
            [dataset, k, kt, status, row_count, now, now],
        )
    else:
        conn.execute(
            """
            INSERT INTO sync_watermark (dataset, key, key_type, status, row_count, last_success_at, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT (dataset, key) DO UPDATE SET
                status=EXCLUDED.status,
                row_count=COALESCE(EXCLUDED.row_count, sync_watermark.row_count),
                last_error=EXCLUDED.last_error,
                key_type=COALESCE(EXCLUDED.key_type, sync_watermark.key_type),
                updated_at=EXCLUDED.updated_at
            """,
            [dataset, k, kt, status, row_count, error, now],
        )


def get_watermark(dataset: str, key: str | date | datetime) -> dict | None:
    init_tables()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT dataset, key, key_type, status, row_count, last_success_at, last_error, updated_at
        FROM sync_watermark WHERE dataset = ? AND key = ?
        """,
        [dataset, _key_str(key)],
    ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_watermarks(dataset: str | None = None, limit: int = 50) -> list[dict]:
    init_tables()
    conn = get_conn()
    if dataset:
        rows = conn.execute(
            """
            SELECT dataset, key, key_type, status, row_count, last_success_at, last_error, updated_at
            FROM sync_watermark WHERE dataset = ?
            ORDER BY key DESC LIMIT ?
            """,
            [dataset, limit],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT dataset, key, key_type, status, row_count, last_success_at, last_error, updated_at
            FROM sync_watermark
            ORDER BY updated_at DESC NULLS LAST, dataset, key DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def latest_ok_key(dataset: str) -> str | None:
    """返回该数据集最新 status=ok 的 key（按字符串倒序，日线 YYYY-MM-DD 可用）。"""
    init_tables()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT key FROM sync_watermark
        WHERE dataset = ? AND status = ?
        ORDER BY key DESC LIMIT 1
        """,
        [dataset, STATUS_OK],
    ).fetchone()
    return str(row[0]) if row else None


def ok_keys(dataset: str, since: str | date | None = None) -> set[str]:
    init_tables()
    conn = get_conn()
    if since is not None:
        rows = conn.execute(
            """
            SELECT key FROM sync_watermark
            WHERE dataset = ? AND status = ? AND key >= ?
            """,
            [dataset, STATUS_OK, _key_str(since)],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT key FROM sync_watermark
            WHERE dataset = ? AND status = ?
            """,
            [dataset, STATUS_OK],
        ).fetchall()
    return {str(r[0]) for r in rows}


def find_daily_gaps(
    expected_dates: Iterable[date],
    dataset: str = DATASET_DAILY,
) -> list[date]:
    """在期望交易日列表中找出尚无 watermark=ok 的日期。"""
    expected = sorted({d if isinstance(d, date) else date.fromisoformat(str(d)[:10]) for d in expected_dates})
    if not expected:
        return []
    have = ok_keys(dataset, since=expected[0])
    return [d for d in expected if d.isoformat() not in have]


def summarize(dataset: str | None = None) -> dict:
    """供 sync status 展示的水位摘要。"""
    init_tables()
    conn = get_conn()
    if dataset:
        rows = conn.execute(
            """
            SELECT status, COUNT(*), MAX(key), MAX(last_success_at)
            FROM sync_watermark WHERE dataset = ?
            GROUP BY status
            """,
            [dataset],
        ).fetchall()
        latest = latest_ok_key(dataset)
        by_status = {str(r[0]): int(r[1]) for r in rows}
        return {
            "dataset": dataset,
            "by_status": by_status,
            "latest_ok_key": latest,
            "ok_count": by_status.get(STATUS_OK, 0),
        }

    datasets = conn.execute(
        "SELECT DISTINCT dataset FROM sync_watermark ORDER BY dataset"
    ).fetchall()
    return {str(r[0]): summarize(str(r[0])) for r in datasets}


def _row_to_dict(row) -> dict:
    return {
        "dataset": row[0],
        "key": str(row[1]),
        "key_type": row[2],
        "status": row[3],
        "row_count": row[4],
        "last_success_at": str(row[5]) if row[5] else None,
        "last_error": row[6],
        "updated_at": str(row[7]) if row[7] else None,
    }
