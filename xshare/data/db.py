"""DuckDB 连接 & 表管理（OLAP：行情/财务/基金/新闻/stock_basic）

连接模型
--------
每次 ``get_conn()`` 返回一个**新的** DuckDB 连接，调用方用完即弃（局部变量，
GC 回收）。DuckDB 对同一文件的多个连接支持并发读；写由 DuckDB 文件级锁
串行化，无需外部 RLock。

历史上这里曾用一把进程级 RLock + _LockedConn/_LockedResult 代理把单个缓存
连接串行化，原因是 DuckDB 单连接在 CPython 下不可重入。改用 per-call 连接
后该问题不复存在——每个调用拿到独立连接，天然无争用。

OLTP 表（sync_task_queue / sync_config / portfolio）在 sqlite_db.py。
"""

import os
from pathlib import Path

import duckdb

# 默认数据库路径：项目根目录下 data/xshare.duckdb
DEFAULT_DB_PATH = os.environ.get(
    "XSHARE_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "xshare.duckdb"),
)


# 测试注入钩子：非 None 时 get_conn() 返回它（内存连接），而非新开磁盘连接。
# 生产恒为 None。之所以用模块级变量而非 monkeypatch get_conn 本身，是因为多数
# 调用方在模块加载时 `from xshare.data.db import get_conn` 已绑定函数引用，
# patch db.get_conn 改不到调用方持有的引用；而 get_conn 读本变量在调用时求值。
_test_conn: duckdb.DuckDBPyConnection | None = None


def get_conn() -> duckdb.DuckDBPyConnection:
    """每次返回一个新的 DuckDB 连接（per-call）。

    调用方用完即弃。同一函数内的 register/INSERT/SELECT 全在返回的局部
    conn 上完成，无需跨函数复用。测试设置 ``db._test_conn`` 指向内存连接，
    所有调用便共享同一库（含原始 SQL 探针）。
    """
    if _test_conn is not None:
        return _test_conn
    db_path = Path(DEFAULT_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def close() -> None:
    """per-call 模式下无缓存连接可关；保留签名兼容调用方（cli / shutdown）。"""
    return None


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

-- 指数基本信息
CREATE TABLE IF NOT EXISTS index_basic (
    code        VARCHAR PRIMARY KEY,  -- 如 000001.SH
    name        VARCHAR NOT NULL,
    market      VARCHAR,              -- SSE / SZSE / CSI / ...
    publisher   VARCHAR,
    category    VARCHAR,
    base_date   DATE,
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

-- 指数日线行情
CREATE TABLE IF NOT EXISTS index_daily (
    code        VARCHAR NOT NULL,
    trade_date  DATE NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    amount      DOUBLE,
    pct_chg     DOUBLE,
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

-- ETF 基础信息（Tushare etf_basic）
CREATE TABLE IF NOT EXISTS etf_basic (
    code        VARCHAR PRIMARY KEY,  -- 如 510300.SH
    name        VARCHAR NOT NULL,     -- 中文简称
    extname     VARCHAR,              -- 扩位简称
    index_code  VARCHAR,
    index_name  VARCHAR,
    exchange    VARCHAR,              -- SH / SZ
    mgr_name    VARCHAR,
    etf_type    VARCHAR,              -- 境内 / QDII
    list_status VARCHAR,              -- L / D / P
    setup_date  DATE,
    list_date   DATE,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

-- ETF 日线行情（Tushare fund_daily）
CREATE TABLE IF NOT EXISTS fund_daily (
    code        VARCHAR NOT NULL,
    trade_date  DATE NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    amount      DOUBLE,
    pct_chg     DOUBLE,
    PRIMARY KEY (code, trade_date)
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

-- 交易日历（本地判定交易日，减少盘中打 trade_cal API）
CREATE TABLE IF NOT EXISTS trade_cal (
    cal_date    DATE PRIMARY KEY,
    is_open     BOOLEAN NOT NULL,
    pretrade_date DATE,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

-- 每日指标（PE/PB/换手率等，按日全市场批量入库）
CREATE TABLE IF NOT EXISTS stock_daily_basic (
    code        VARCHAR NOT NULL,
    trade_date  DATE NOT NULL,
    pe          DOUBLE,
    pb          DOUBLE,
    ps          DOUBLE,
    total_mv    DOUBLE,
    circ_mv     DOUBLE,
    turnover_rate DOUBLE,
    PRIMARY KEY (code, trade_date)
);

-- 数据集水位：日线以 trade_date 为 key；全量类用 key='ALL'
CREATE TABLE IF NOT EXISTS sync_watermark (
    dataset          VARCHAR NOT NULL,  -- daily | stock_basic | finance | ...
    key              VARCHAR NOT NULL,  -- YYYY-MM-DD 或 ALL 或股票代码
    key_type         VARCHAR,           -- date | all | code（显式语义，消除多义）
    status           VARCHAR NOT NULL,  -- pending|ok|partial|error
    row_count        INTEGER,
    last_success_at  TIMESTAMP,
    last_error       VARCHAR,
    updated_at       TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (dataset, key)
);

-- 迁移：为旧库补 key_type 列（CREATE IF NOT EXISTS 不会给已有表加列）
ALTER TABLE sync_watermark ADD COLUMN IF NOT EXISTS key_type VARCHAR;

-- 回填 key_type：按 dataset 推断
UPDATE sync_watermark SET key_type = CASE
    WHEN dataset IN ('daily', 'index_daily', 'fund_daily', 'daily_basic',
                     'moneyflow', 'sector_moneyflow', 'market_moneyflow',
                     'limit_list', 'top_list', 'concept_board', 'concept_member') THEN 'date'
    WHEN dataset IN ('stock_basic', 'index_basic', 'etf_basic', 'trade_cal', 'news') THEN 'all'
    WHEN dataset IN ('finance', 'fund_nav') THEN 'code'
    ELSE NULL
END WHERE key_type IS NULL;

-- 旧版曾把 sync_config / sync_task_queue / portfolio 放在 DuckDB，
-- 迁到 SQLite（xshare/data/sqlite_db.py）后这里 DROP 掉旧表，避免歧义。
-- OLAP 表的数据保留不动。
DROP TABLE IF EXISTS sync_config;
DROP TABLE IF EXISTS sync_task_queue;
DROP TABLE IF EXISTS portfolio;
DROP SEQUENCE IF EXISTS portfolio_id_seq;
DROP SEQUENCE IF EXISTS task_queue_id_seq;

-- 资产日线元数据（入库时增量维护，避免列表/覆盖率实时全表 GROUP BY）
-- window_count / sufficient 基于"最近 252 交易日"窗口，次新按实际上市日折算。
CREATE TABLE IF NOT EXISTS code_meta (
    code              VARCHAR PRIMARY KEY,
    asset_type        VARCHAR NOT NULL,   -- stock | etf | index
    data_count        INTEGER,            -- 该 code 日线总行数
    first_trade_date  DATE,               -- MIN(trade_date)
    latest_trade_date DATE,               -- MAX(trade_date)
    has_one_year_data BOOLEAN,            -- date_diff(first, latest) >= 365
    window_count      INTEGER,            -- 最近 252 交易日行数
    window_expected   INTEGER,            -- 该 code 在窗口内自上市日起应有交易日数
    sufficient        BOOLEAN,            -- window_count >= threshold * window_expected
    updated_at        TIMESTAMP DEFAULT current_timestamp
);

-- 实时行情快照（quote 同步任务交易时段每 5 分钟写入；读路径缓存优先）
CREATE TABLE IF NOT EXISTS quote_snapshot (
    code          VARCHAR NOT NULL,
    name          VARCHAR,
    price         DOUBLE,
    change_pct    DOUBLE,
    change_amount DOUBLE,
    open          DOUBLE,
    high          DOUBLE,
    low           DOUBLE,
    prev_close    DOUBLE,
    volume        DOUBLE,
    amount        DOUBLE,
    turnover      DOUBLE,
    pe            DOUBLE,
    pb            DOUBLE,
    total_mv      DOUBLE,
    ts            TIMESTAMP NOT NULL,
    PRIMARY KEY (code, ts)
);

CREATE TABLE IF NOT EXISTS index_snapshot (
    code       VARCHAR NOT NULL,
    name       VARCHAR,
    price      DOUBLE,
    change_pct DOUBLE,
    ts         TIMESTAMP NOT NULL,
    PRIMARY KEY (code, ts)
);

CREATE TABLE IF NOT EXISTS sector_snapshot (
    name       VARCHAR NOT NULL,
    change_pct DOUBLE,
    leader     VARCHAR,
    leader_pct DOUBLE,
    ts         TIMESTAMP NOT NULL,
    PRIMARY KEY (name, ts)
);

-- 个股资金流向（Tushare moneyflow，金额单位：万元）
CREATE TABLE IF NOT EXISTS stock_moneyflow (
    code            VARCHAR NOT NULL,
    trade_date      DATE NOT NULL,
    buy_sm_amount   DOUBLE,
    sell_sm_amount  DOUBLE,
    buy_md_amount   DOUBLE,
    sell_md_amount  DOUBLE,
    buy_lg_amount   DOUBLE,
    sell_lg_amount  DOUBLE,
    buy_elg_amount  DOUBLE,
    sell_elg_amount DOUBLE,
    net_mf_amount   DOUBLE,
    PRIMARY KEY (code, trade_date)
);

-- 板块资金流向（Tushare moneyflow_ind_dc，金额单位：元）
CREATE TABLE IF NOT EXISTS sector_moneyflow (
    trade_date       DATE NOT NULL,
    content_type     VARCHAR NOT NULL,
    code             VARCHAR NOT NULL,
    name             VARCHAR,
    pct_change       DOUBLE,
    net_amount       DOUBLE,
    buy_elg_amount   DOUBLE,
    buy_lg_amount    DOUBLE,
    buy_md_amount    DOUBLE,
    buy_sm_amount    DOUBLE,
    buy_sm_stock     VARCHAR,
    PRIMARY KEY (trade_date, content_type, code)
);

-- 大盘资金流向（Tushare moneyflow_mkt_dc，金额单位：元）
CREATE TABLE IF NOT EXISTS market_moneyflow (
    trade_date         DATE PRIMARY KEY,
    pct_change_sh      DOUBLE,
    pct_change_sz      DOUBLE,
    net_amount         DOUBLE,
    buy_elg_amount     DOUBLE,
    buy_lg_amount      DOUBLE,
    buy_md_amount      DOUBLE,
    buy_sm_amount      DOUBLE
);

-- 涨跌停列表（Tushare limit_list_d，含连板数/封单额/炸板次数）
CREATE TABLE IF NOT EXISTS limit_list (
    trade_date     DATE NOT NULL,
    code           VARCHAR NOT NULL,
    name           VARCHAR,
    industry       VARCHAR,
    close          DOUBLE,
    pct_chg        DOUBLE,
    amount         DOUBLE,
    limit_amount   DOUBLE,
    float_mv       DOUBLE,
    turnover_ratio DOUBLE,
    first_time     VARCHAR,
    last_time      VARCHAR,
    open_times     INTEGER,
    up_stat        VARCHAR,
    limit_times    INTEGER,
    limit_type     VARCHAR,
    PRIMARY KEY (trade_date, code, limit_type)
);

-- 龙虎榜（Tushare top_list）
CREATE TABLE IF NOT EXISTS top_list (
    trade_date     DATE NOT NULL,
    code           VARCHAR NOT NULL,
    name           VARCHAR,
    close          DOUBLE,
    pct_change     DOUBLE,
    turnover_rate  DOUBLE,
    amount         DOUBLE,
    l_buy          DOUBLE,
    l_sell         DOUBLE,
    l_amount       DOUBLE,
    net_amount     DOUBLE,
    net_rate       DOUBLE,
    amount_rate    DOUBLE,
    float_values   DOUBLE,
    reason         VARCHAR,
    PRIMARY KEY (trade_date, code)
);

-- 概念题材板块（Tushare dc_concept）
CREATE TABLE IF NOT EXISTS concept_board (
    trade_date         DATE NOT NULL,
    code               VARCHAR NOT NULL,
    name               VARCHAR,
    pct_change         DOUBLE,
    hot                DOUBLE,
    sort               INTEGER,
    strength           DOUBLE,
    zt_num             INTEGER,
    main_change        DOUBLE,
    lead_stock         VARCHAR,
    lead_stock_code    VARCHAR,
    lead_stock_pct     DOUBLE,
    PRIMARY KEY (trade_date, code)
);

-- 概念题材成分股（Tushare dc_concept_cons）
CREATE TABLE IF NOT EXISTS concept_member (
    trade_date    DATE NOT NULL,
    code          VARCHAR NOT NULL,
    name          VARCHAR,
    concept_code  VARCHAR NOT NULL,
    industry      VARCHAR,
    reason        VARCHAR,
    hot_num       INTEGER,
    PRIMARY KEY (trade_date, code, concept_code)
);
"""


def init_tables(conn: duckdb.DuckDBPyConnection | None = None):
    """创建所有表（幂等）。

    首次创建 code_meta 后，若 daily 表已有存量数据则全量回填，
    避免老库升级时列表/覆盖率显示空。回填只在 code_meta 为空时触发，
    后续增量由日线入库 hook 维护。
    """
    c = conn or get_conn()
    c.execute(SCHEMA_SQL)
    try:
        meta_count = c.execute("SELECT COUNT(*) FROM code_meta").fetchone()[0]
    except Exception:
        meta_count = 0
    if meta_count == 0:
        try:
            has_daily = c.execute(
                "SELECT COUNT(*) FROM ("
                " SELECT 1 FROM stock_daily LIMIT 1"
                " UNION ALL SELECT 1 FROM fund_daily LIMIT 1"
                " UNION ALL SELECT 1 FROM index_daily LIMIT 1"
                ")"
            ).fetchone()[0]
        except Exception:
            has_daily = 0
        if has_daily:
            from xshare.data.sources.tushare_source import rebuild_code_meta_all
            rebuild_code_meta_all(c)
