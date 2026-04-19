"""DuckDB 连接 & 表管理"""

import os
from pathlib import Path

import duckdb

# 默认数据库路径：项目根目录下 data/xshare.duckdb
DEFAULT_DB_PATH = os.environ.get(
    "XSHARE_DB_PATH",
    str(Path(__file__).resolve().parents[3] / "data" / "xshare.duckdb"),
)

_conn: duckdb.DuckDBPyConnection | None = None


def get_conn() -> duckdb.DuckDBPyConnection:
    """获取全局 DuckDB 连接（惰性初始化）"""
    global _conn
    if _conn is None:
        db_path = Path(DEFAULT_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(db_path))
    return _conn


def close():
    """关闭连接"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# --------------- 表结构定义 ---------------

SCHEMA_SQL = """
-- 股票基本信息
CREATE TABLE IF NOT EXISTS stock_basic (
    code        VARCHAR PRIMARY KEY,  -- 如 002594.SZ
    name        VARCHAR NOT NULL,
    market      VARCHAR,              -- SH / SZ / BJ
    industry    VARCHAR,
    list_date   DATE,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

-- 日线行情
CREATE TABLE IF NOT EXISTS stock_daily (
    code        VARCHAR NOT NULL,
    trade_date  DATE NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      BIGINT,
    amount      DOUBLE,
    turnover    DOUBLE,
    PRIMARY KEY (code, trade_date)
);

-- 财务数据（季度）
CREATE TABLE IF NOT EXISTS stock_finance (
    code        VARCHAR NOT NULL,
    end_date    DATE NOT NULL,       -- 报告期末 如 2024-03-31
    pe          DOUBLE,
    pb          DOUBLE,
    roe         DOUBLE,
    revenue     DOUBLE,
    net_profit  DOUBLE,
    revenue_yoy DOUBLE,
    profit_yoy  DOUBLE,
    updated_at  TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (code, end_date)
);

-- 基金净值
CREATE TABLE IF NOT EXISTS fund_nav (
    code        VARCHAR NOT NULL,
    nav_date    DATE NOT NULL,
    nav         DOUBLE,              -- 单位净值
    acc_nav     DOUBLE,              -- 累计净值
    PRIMARY KEY (code, nav_date)
);

-- 基金基本信息
CREATE TABLE IF NOT EXISTS fund_basic (
    code        VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    fund_type   VARCHAR,
    manager     VARCHAR,
    size        DOUBLE,              -- 规模（亿）
    setup_date  DATE,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

-- 新闻
CREATE TABLE IF NOT EXISTS news (
    id            VARCHAR PRIMARY KEY,   -- URL hash
    publish_time  TIMESTAMP,
    source        VARCHAR,
    title         VARCHAR,
    content       VARCHAR,               -- 正文摘要（前 500 字）
    stock_codes   VARCHAR[],
    tags          VARCHAR[]
);

-- 持仓交易流水（买入卖出都是记录，买入 quantity>0，卖出 quantity<0）
CREATE SEQUENCE IF NOT EXISTS portfolio_id_seq START 1;
CREATE TABLE IF NOT EXISTS portfolio (
    id          INTEGER DEFAULT nextval('portfolio_id_seq') PRIMARY KEY,
    code        VARCHAR NOT NULL,
    name        VARCHAR,
    direction   VARCHAR NOT NULL,    -- buy / sell
    trade_date  DATE NOT NULL,
    price       DOUBLE,
    quantity    INTEGER,             -- 正数=买入，负数=卖出
    amount      DOUBLE,              -- price * abs(quantity)
    memo        VARCHAR,             -- 备注
    updated_at  TIMESTAMP DEFAULT current_timestamp
);
"""


def init_tables(conn: duckdb.DuckDBPyConnection | None = None):
    """创建所有表（幂等）"""
    c = conn or get_conn()
    c.execute(SCHEMA_SQL)
