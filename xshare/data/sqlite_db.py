"""SQLite 连接 & OLTP 表管理（任务队列、配置、持仓流水）

与 DuckDB 的分工
----------------
DuckDB（db.py）管 OLAP：行情、财务、基金、新闻等批量分析表。
本模块管 OLTP：sync_task_queue / sync_config / portfolio —— 点状写、
要事务、lease 心跳与状态流转，正是 SQLite WAL 的主场。

连接与并发
----------
全局共享一个 Connection（``check_same_thread=False``），供 asyncio 事件循环
与 ``asyncio.to_thread`` 线程池共用。Python sqlite3 要求同一 Connection 的
访问由调用方串行化——这里用进程级 RLock：单条 ``execute`` 自动加锁；
多语句事务（如抢任务）用 ``sqlite_critical()`` 包住整段 BEGIN/COMMIT。

时间戳统一用 UTC 文本 ``YYYY-MM-DD HH:MM:SS``：SQL 端 ``current_timestamp``
与 ``datetime('now')`` 都产生此格式，Python 端用 ``utcnow`` / ``now_ts()``，
保证字符串比较（如 lease_at < cutoff）正确。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_SQLITE_PATH = os.environ.get(
    "XSHARE_SQLITE_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "xshare.sqlite"),
)

_raw_conn: sqlite3.Connection | None = None
_init_lock = threading.Lock()
_op_lock = threading.RLock()


class _LockedConn:
    """对共享 sqlite3.Connection 的薄代理：每条 API 调用持有 RLock。"""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def get_sqlite_conn() -> _LockedConn:
    """获取全局 SQLite 连接（惰性初始化，WAL 模式）。

    check_same_thread=False：worker 通过 asyncio.to_thread 在线程池里
    调用，连接需跨线程共享。所有操作经 RLock 串行化。
    """
    global _raw_conn
    if _raw_conn is None:
        with _init_lock:
            if _raw_conn is None:
                db_path = Path(DEFAULT_SQLITE_PATH)
                db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    str(db_path),
                    check_same_thread=False,
                    isolation_level=None,  # autocommit：事务由调用方显式 BEGIN/COMMIT
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                _raw_conn = conn
    return _LockedConn(_raw_conn, _op_lock)


@contextmanager
def sqlite_critical() -> Iterator[_LockedConn]:
    """持有连接锁的临界区，用于多语句事务（避免 BEGIN 与 COMMIT 之间被插队）。"""
    with _op_lock:
        yield get_sqlite_conn()


def close_sqlite() -> None:
    global _raw_conn
    if _raw_conn is not None:
        with _init_lock:
            if _raw_conn is not None:
                with _op_lock:
                    _raw_conn.close()
                    _raw_conn = None


def now_ts() -> str:
    """当前 UTC 时间戳字符串，与 SQL 的 current_timestamp / datetime('now') 同格式。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ts(dt: datetime) -> str:
    """把 datetime 转成与 SQL 时间戳同格式的字符串。

    若 dt 带时区，先转 UTC；无时区则按 UTC 朴素时间处理（调用方应传 UTC）。
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# --------------- 表结构定义 ---------------

SQLITE_SCHEMA_SQL = """
-- 同步任务配置与运行状态
CREATE TABLE IF NOT EXISTS sync_config (
    job              TEXT PRIMARY KEY,
    enabled          INTEGER DEFAULT 1,   -- 0/1 代替 BOOLEAN
    interval_minutes INTEGER NOT NULL,
    last_run_at      TEXT,
    last_status      TEXT,
    last_error       TEXT,
    updated_at       TEXT DEFAULT (datetime('now'))
);

-- 异步任务队列
CREATE TABLE IF NOT EXISTS sync_task_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT NOT NULL,
    payload       TEXT DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued',
    priority      INTEGER DEFAULT 5,
    trigger       TEXT DEFAULT 'manual',
    attempts      INTEGER DEFAULT 0,
    max_attempts  INTEGER DEFAULT 3,
    queued_at     TEXT DEFAULT (datetime('now')),
    started_at    TEXT,
    finished_at   TEXT,
    next_run_at   TEXT,
    result        TEXT,
    last_error    TEXT,
    lease_at      TEXT
);

-- 持仓交易流水
CREATE TABLE IF NOT EXISTS portfolio (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    name        TEXT,
    direction   TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    price       REAL,
    quantity    INTEGER,
    amount      REAL,
    memo        TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);
"""


def init_sqlite_tables(conn: sqlite3.Connection | _LockedConn | None = None) -> None:
    """创建所有 OLTP 表（幂等）。"""
    c = conn or get_sqlite_conn()
    c.executescript(SQLITE_SCHEMA_SQL)
