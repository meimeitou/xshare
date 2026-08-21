"""数据源适配层 - Tushare（批量同步写入 DuckDB + 水位）。"""

from __future__ import annotations

import logging
import os
import time
import json
from datetime import date, datetime, timedelta

import pandas as pd
import tushare as ts

from xshare.data import rate_limit
from xshare.data import watermark as wm
from xshare.data.sources.tushare_client import TushareClient

logger = logging.getLogger(__name__)

_pro: ts.pro_api | None = None


def _log_start(job: str, **kwargs) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    logger.info("[sync] %s 开始%s", job, f" {parts}" if parts else "")


def _log_progress(job: str, msg: str, *args) -> None:
    logger.info("[sync] %s " + msg, job, *args)


def _log_done(job: str, table: str, rows: int, **kwargs) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    logger.info(
        "[sync] %s 完成 table=%s rows=%d%s",
        job, table, rows, f" {parts}" if parts else "",
    )


def _log_skip(job: str, reason: str) -> None:
    logger.info("[sync] %s 跳过 reason=%s", job, reason)


# ─── 多 code 批量拉取 ─────────────────────────────────────────────────────────


# Tushare 单次响应硬上限 6000 行；按目标行数估算分批，留余量避免被截断。
_BATCH_ROW_LIMIT = 6000
_BATCH_ROW_SAFETY = 0.9  # 估算时只用到 90% 上限，防止行数略超导致截断丢数据


def _multi_code_batches(
    codes: list[str],
    dates: list[date],
    method: str,
) -> list[list[str]]:
    """将 codes 切成若干批，每批一次 ``method(ts_code="A,B,C", start, end)`` 调用。

    分批依据：每批预估行数 = 批内 code 数 × 日期数 × 安全系数，
    不超过 6000 行硬上限。所有批共用 [min(dates), max(dates)] 区间。

    返回批列表（每批是 code 子列表）。空 codes 或空 dates 返回 []。
    """
    if not codes or not dates:
        return []
    n_dates = len(dates)
    # 单 code 在区间内约 n_dates 行；安全系数后每批最多多少个 code
    max_codes_per_batch = max(1, int(_BATCH_ROW_LIMIT * _BATCH_ROW_SAFETY / n_dates))
    batches: list[list[str]] = []
    for i in range(0, len(codes), max_codes_per_batch):
        batches.append(codes[i:i + max_codes_per_batch])
    return batches


def _fetch_multi_codes_batched(
    method: str,
    codes: list[str],
    start_ymd: str,
    end_ymd: str,
    dates: list[date],
    upsert_fn,
    job_label: str,
) -> tuple[int, list[str]]:
    """多 code 批量拉取并 upsert，返回 (写入行数, 未能批量拉取的 code 列表)。

    策略：
    1. 按 _multi_code_batches 切批，每批一次调用，ts_code 用逗号拼接。
    2. 若某批调用异常或返回为空（可能批量超限/权限不足），降级为对该批
       逐个 code 单独拉取，保证不丢数据。
    """
    total = 0
    fallback_codes: list[str] = []
    batches = _multi_code_batches(codes, dates, method)
    _log_progress(
        job_label, "多 code 批量：%d 个 code 分 %d 批，区间 %s..%s",
        len(codes), len(batches), start_ymd, end_ymd,
    )
    for bi, batch in enumerate(batches, start=1):
        ts_codes = ",".join(batch)
        try:
            df = _pro_call(method, ts_code=ts_codes, start_date=start_ymd, end_date=end_ymd)
            if df is None or df.empty:
                # 空响应：可能是该批全部停牌/无数据，也可能是批量被拒。
                # 降级逐只重试以区分。
                logger.debug(
                    "[sync] %s 批 %d/%d 空响应，降级逐只 (%d codes)",
                    job_label, bi, len(batches), len(batch),
                )
                fallback_codes.extend(batch)
                continue
            total += upsert_fn(df)
        except Exception as exc:
            logger.debug(
                "[sync] %s 批 %d/%d 异常：%s，降级逐只",
                job_label, bi, len(batches), exc,
            )
            fallback_codes.extend(batch)
        if bi % 20 == 0 or bi == len(batches):
            _log_progress(job_label, "批量进度 %d/%d 批 rows=%d", bi, len(batches), total)

    # 降级逐只
    if fallback_codes:
        _log_progress(
            job_label, "降级逐只拉取 %d 个 code", len(fallback_codes),
        )
        for c in fallback_codes:
            try:
                df = _pro_call(method, ts_code=c, start_date=start_ymd, end_date=end_ymd)
                if df is None or df.empty:
                    continue
                total += upsert_fn(df)
            except Exception as exc:
                logger.debug("[sync] %s 逐只 %s 失败: %s", job_label, c, exc)
                continue
    return total, fallback_codes


def _get_pro() -> ts.pro_api:
    """获取 Tushare Pro API"""
    global _pro
    if _pro is None:
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("请设置环境变量 TUSHARE_TOKEN")
        _pro = ts.pro_api(token)
    return _pro


def _http_call(api_name: str, token: str, params: dict, fields: str = "", timeout: float = 30.0):
    """直连 Tushare HTTP API（HTTPS, api.tushare.pro），绕开 tushare SDK 的明文 HTTP 端点。

    tushare 1.4.x SDK 默认走 ``http://api.waditu.com/dataapi``，该明文端点在
    部分网络环境下会持续 ``Connection reset by peer``；官方 HTTPS 端点
    ``https://api.tushare.pro`` 稳定。此处用 stdlib urllib 每次新连接，
    无 keep-alive 残连问题。返回与 SDK 一致的 DataFrame。
    """
    import urllib.request
    import urllib.error

    body = json.dumps({
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tushare.pro",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"Tushare HTTP {e.code}: {e.reason}") from e
    data = json.loads(raw)
    if data.get("code") != 0:
        msg = data.get("msg") or f"Tushare code={data.get('code')}"
        if rate_limit.classify_tushare_error(Exception(msg)) is rate_limit.ErrorType.RATE_LIMIT:
            raise rate_limit.TushareRateLimitError(msg)
        raise Exception(msg)
    payload = data.get("data") or {}
    cols = payload.get("fields") or []
    items = payload.get("items") or []
    return pd.DataFrame(items, columns=cols)


# 优先使用 HTTPS 直连（避免 SDK 明文端点 Connection reset）；可用环境变量关闭。
# 运行时读取，便于测试 monkeypatch 注入。
def _use_https_direct() -> bool:
    return os.environ.get("XSHARE_TUSHARE_HTTPS_DIRECT", "1") not in ("0", "false", "False")


def _tushare_caller(method: str, kwargs: dict) -> pd.DataFrame:
    """传输层调用器：HTTPS 直连或 SDK，调用时从模块全局解析。

    供 ``TushareClient`` 使用——测试可通过 monkeypatch ``_use_https_direct``
    / ``_http_call`` / ``_get_pro`` 注入桩件，本函数在调用时才解析这些引用。
    """
    if _use_https_direct():
        fields = kwargs.get("fields") or ""
        call_kwargs = {k: v for k, v in kwargs.items() if k != "fields"}
        token = os.environ.get("TUSHARE_TOKEN", "")
        return _http_call(method, token, call_kwargs, fields=fields)
    pro = _get_pro()
    fn = getattr(pro, method)
    return fn(**kwargs)


def _pro_call(method: str, **kwargs):
    """带全局限速 + 频率超限/瞬时网络错误退避重试的 Tushare 调用。

    委托 ``TushareClient`` 实现限速/重试逻辑，传输层由 ``_tushare_caller``
    决定（HTTPS 直连或 SDK）。每次调用新建客户端，确保环境变量实时生效。
    """
    client = TushareClient(_tushare_caller)
    return client.call(method, **kwargs)

# ─── stock_basic ─────────────────────────────────────────────────────────────


def fetch_stock_basic() -> pd.DataFrame:
    """获取A股股票基本信息"""
    df = _pro_call(
        "stock_basic",
        exchange="",
        list_status="L",
        fields="ts_code,name,market,industry,list_date",
    )
    df = df.rename(columns={"ts_code": "code"})
    df["list_date"] = pd.to_datetime(df["list_date"]).dt.date
    return df


def sync_stock_basic_to_db(force: bool = False) -> int:
    """从 Tushare 拉取全量 A 股基础信息并写入本地 DuckDB。"""
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)
    _log_start("stock_basic", force=force, table="stock_basic")

    if not force:
        row = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM stock_basic"
        ).fetchone()
        count, last_update = row
        if count > 0 and last_update is not None:
            age_hours = (datetime.now() - last_update.replace(tzinfo=None)).total_seconds() / 3600
            if age_hours < 24:
                _log_skip("stock_basic", f"本地已有 {count} 条且 {age_hours:.1f}h 内已更新")
                return 0

    try:
        df = fetch_stock_basic()
        if df.empty:
            wm.set_watermark(wm.DATASET_STOCK_BASIC, "ALL", wm.STATUS_ERROR, 0, "empty response")
            _log_skip("stock_basic", "上游返回空")
            return 0

        conn.execute("DELETE FROM stock_basic")
        conn.register("_sb_df", df)
        conn.execute("""
            INSERT INTO stock_basic (code, name, market, industry, list_date, updated_at)
            SELECT code, name, market, industry, list_date, current_timestamp
            FROM _sb_df
        """)
        conn.unregister("_sb_df")
        wm.set_watermark(wm.DATASET_STOCK_BASIC, "ALL", wm.STATUS_OK, len(df))
        _log_done("stock_basic", "stock_basic", len(df), source="tushare.stock_basic")
        return len(df)
    except Exception as exc:
        wm.set_watermark(wm.DATASET_STOCK_BASIC, "ALL", wm.STATUS_ERROR, error=str(exc))
        raise


def upsert_stocks_to_db(df: pd.DataFrame) -> None:
    """将股票基础信息按需回填到本地 DuckDB（逐行 UPSERT）"""
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)
    for _, row in df.iterrows():
        conn.execute(
            """
            INSERT OR REPLACE INTO stock_basic (code, name, market, industry, list_date, updated_at)
            VALUES (?, ?, ?, ?, ?, current_timestamp)
            """,
            [row["code"], row["name"], row.get("market"), row.get("industry"), row.get("list_date")],
        )


# ─── index_basic / index_daily ───────────────────────────────────────────────


def _index_markets() -> list[str]:
    raw = os.environ.get("XSHARE_INDEX_MARKETS", "SSE,SZSE,CSI")
    return [m.strip().upper() for m in raw.split(",") if m.strip()]


def fetch_index_basic(markets: list[str] | None = None) -> pd.DataFrame:
    """拉取指数基础信息（默认 SSE/SZSE/CSI）。"""
    frames: list[pd.DataFrame] = []
    for market in markets or _index_markets():
        df = _pro_call(
            "index_basic",
            market=market,
            fields="ts_code,name,market,publisher,category,base_date,list_date",
        )
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(
            columns=["code", "name", "market", "publisher", "category", "base_date", "list_date"]
        )
    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"ts_code": "code"})
    out = out.drop_duplicates(subset=["code"], keep="first")
    for col in ("base_date", "list_date"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], format="%Y%m%d", errors="coerce").dt.date
    return out


def sync_index_basic_to_db(force: bool = False) -> int:
    """从 Tushare 拉取指数基础信息并写入 index_basic。"""
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)
    markets = _index_markets()
    _log_start("index_basic", force=force, table="index_basic", markets=",".join(markets))

    if not force:
        row = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM index_basic"
        ).fetchone()
        count, last_update = row
        if count > 0 and last_update is not None:
            age_hours = (datetime.now() - last_update.replace(tzinfo=None)).total_seconds() / 3600
            if age_hours < 24:
                _log_skip("index_basic", f"本地已有 {count} 条且 {age_hours:.1f}h 内已更新")
                return 0

    try:
        df = fetch_index_basic()
        if df.empty:
            wm.set_watermark(wm.DATASET_INDEX_BASIC, "ALL", wm.STATUS_ERROR, 0, "empty response")
            _log_skip("index_basic", "上游返回空")
            return 0

        conn.execute("DELETE FROM index_basic")
        conn.register("_ib_df", df)
        conn.execute("""
            INSERT INTO index_basic (code, name, market, publisher, category, base_date, list_date, updated_at)
            SELECT code, name, market, publisher, category, base_date, list_date, current_timestamp
            FROM _ib_df
        """)
        conn.unregister("_ib_df")
        wm.set_watermark(wm.DATASET_INDEX_BASIC, "ALL", wm.STATUS_OK, len(df))
        _log_done("index_basic", "index_basic", len(df), source="tushare.index_basic")
        return len(df)
    except Exception as exc:
        wm.set_watermark(wm.DATASET_INDEX_BASIC, "ALL", wm.STATUS_ERROR, error=str(exc))
        raise


def get_index_daily_coverage(lookback_trading_days: int | None = None) -> dict:
    """统计本地指数日线覆盖率。"""
    from xshare.data.db import get_conn, init_tables

    target = lookback_trading_days
    if target is None:
        target = int(os.environ.get("XSHARE_INDEX_DAILY_MIN_TRADING_DAYS", "252") or "252")
    target = max(1, target)

    conn = get_conn()
    init_tables(conn)
    since = date.today() - timedelta(days=max(target * 2, 365))
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date)
        FROM index_daily
        WHERE trade_date >= ?
        """,
        [since],
    ).fetchone()
    in_db = int(row[0] or 0)
    per_code = _per_code_coverage(
        conn, "index_daily", "index_basic", target, "XSHARE_INDEX_DAILY_CODE_THRESHOLD",
    )
    return {
        "trading_days_in_db": in_db,
        "target_days": target,
        "missing_estimate": max(0, target - in_db),
        "oldest": str(row[1]) if row[1] else None,
        "newest": str(row[2]) if row[2] else None,
        "sufficient": in_db >= target and per_code["sufficient"],
        "per_code": per_code,
        "watermark_latest_ok": wm.latest_ok_key(wm.DATASET_INDEX_DAILY),
        "watermark_ok_count": wm.summarize(wm.DATASET_INDEX_DAILY).get("ok_count", 0),
        **_daily_sync_status(wm.DATASET_INDEX_DAILY),
    }


def find_missing_index_daily_dates(days: int = 252) -> list[date]:
    """期望最近 days 个交易日中，index_daily watermark 尚未 ok 的日期。"""
    pro = None
    try:
        if os.environ.get("TUSHARE_TOKEN"):
            pro = _get_pro()
    except Exception:
        pro = None
    expected = _resolve_trade_days(date.today(), days, pro or type("P", (), {})())
    return wm.find_daily_gaps(expected, dataset=wm.DATASET_INDEX_DAILY)


def sync_index_daily_to_db(
    trade_date: str | None = None,
    days: int = 1,
    code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
) -> int:
    """按 index_basic 列表拉取指数日线并写入 index_daily；水位按交易日。

    Tushare ``index_daily`` 要求 ``ts_code``，因此按指数代码循环，
    每个代码一次拉取日期区间（减少调用次数）。

    日期范围二选一：``start_date``/``end_date`` 按区间补全，或 ``days``
    取最近 N 个交易日。``overwrite=True`` 时强制重拉覆盖已有数据。
    """
    from xshare.data.db import get_conn, init_tables
    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    codes = [code] if code else [r[0] for r in conn.execute("SELECT code FROM index_basic ORDER BY code").fetchall()]
    if not codes:
        _log_progress("index_daily", "本地无 index_basic，先同步基础信息")
        sync_index_basic_to_db(force=True)
        codes = [r[0] for r in conn.execute("SELECT code FROM index_basic ORDER BY code").fetchall()]
    if not codes:
        _log_skip("index_daily", "无指数代码可同步")
        return 0

    range_mode = bool(start_date or end_date)
    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()

    if range_mode:
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else cursor
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else cursor
        if start_d > end_d:
            raise ValueError(f"start_date({start_d}) 不能晚于 end_date({end_d})")
        trade_days = _resolve_trade_days_range(start_d, end_d, pro)
        days = len(trade_days)
    else:
        if days <= 0:
            return 0
        trade_days = _resolve_trade_days(cursor, days, pro)
    if not trade_days:
        _log_skip("index_daily", "无交易日")
        return 0

    ok_wm = wm.ok_keys(wm.DATASET_INDEX_DAILY, since=min(trade_days))
    need_days: list[date] = []
    for idx, d in enumerate(trade_days):
        # overwrite 模式或最新交易日（idx==0）一律重拉；其余已有 ok 水位的跳过
        if overwrite or idx == 0 or d.isoformat() not in ok_wm:
            need_days.append(d)

    if not need_days:
        _log_skip("index_daily", f"目标交易日均已有水位 days={days}")
        return 0

    start_ymd = min(need_days).strftime("%Y%m%d")
    end_ymd = max(need_days).strftime("%Y%m%d")
    need_set = {d for d in need_days}
    fetched_rows = 0
    _log_start(
        "index_daily",
        table="index_daily",
        codes=len(codes),
        range=f"{start_ymd}..{end_ymd}",
        dates=len(need_days),
        overwrite=overwrite,
        range_mode=range_mode,
    )

    # 单 code 缺口检测：若缺失 >1 段，直接拉一年区间覆盖，减少调用次数
    if code and len(codes) == 1:
        year_lookback = int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
        year_days = _resolve_trade_days(cursor, year_lookback, pro)
        year_have = _existing_dates_for_code(conn, "index_daily", code, year_days)
        year_missing = _missing_segments_from_have(year_days, year_have)
        if len(year_missing) > 1:
            _log_progress(
                "index_daily", "code=%s 缺失 %d 段，按一年区间拉取 %s..%s",
                code, len(year_missing),
                min(year_days).isoformat(), max(year_days).isoformat(),
            )
            df = _pro_call(
                "index_daily", ts_code=code,
                start_date=min(year_days).strftime("%Y%m%d"),
                end_date=max(year_days).strftime("%Y%m%d"),
            )
            fetched_rows = _upsert_index_daily(conn, df)
            for d in need_days:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM index_daily WHERE trade_date = ?", [d]
                ).fetchone()[0]
                if cnt and int(cnt) > 0:
                    wm.set_watermark(wm.DATASET_INDEX_DAILY, d, wm.STATUS_OK, int(cnt))
            _log_done(
                "index_daily", "index_daily", fetched_rows,
                dates_ok=len(need_days), codes=1, source="tushare.index_daily.range",
            )
            return fetched_rows

    # 多 code 拉取：优先按 trade_date 全市场单日拉取（Tushare index_daily 支持
    # trade_date 入参，单次返回全市场约 1100+ 指数）；仅当 trade_date 调用失败
    # 或返回空时，回退到按 index_basic 代码逐只区间拉取。
    # 旧的"ts_code 逗号拼接多 code 批量"路径经实测 Tushare 对多 ts_code 拼接
    # 返回空，故弃用，改回退为逐只拉取。
    fetched_rows = 0

    def _upsert_df(df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        df = df.rename(columns={"ts_code": "code", "vol": "volume"})
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
        df = df[df["trade_date"].isin(need_set)]
        if df.empty:
            return 0
        return _upsert_index_daily(conn, df)

    # 单日场景：逐日 trade_date 全市场拉取（最快）
    use_trade_date = len(need_days) <= 3 and code is None
    if use_trade_date:
        for d in need_days:
            yyyymmdd = d.strftime("%Y%m%d")
            try:
                df = _pro_call("index_daily", trade_date=yyyymmdd)
            except Exception as exc:
                logger.debug("[sync] index_daily trade_date=%s 失败，回退逐只: %s", yyyymmdd, exc)
                df = None
            if df is None or df.empty:
                continue
            fetched_rows += _upsert_df(df)

    # 多日场景或 trade_date 未覆盖：按 code 区间拉取（逐只，区间 [start_ymd, end_ymd]）
    if code is None and (not use_trade_date or fetched_rows == 0):
        _log_progress(
            "index_daily", "按 %d 个 code 区间拉取 %s..%s",
            len(codes), start_ymd, end_ymd,
        )
        for c in codes:
            try:
                df = _pro_call("index_daily", ts_code=c, start_date=start_ymd, end_date=end_ymd)
            except Exception as exc:
                logger.debug("[sync] index_daily %s 失败: %s", c, exc)
                continue
            if df is None or df.empty:
                continue
            fetched_rows += _upsert_df(df)

    dates_ok = 0
    for d in need_days:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM index_daily WHERE trade_date = ?", [d]
        ).fetchone()[0]
        if cnt and int(cnt) > 0:
            wm.set_watermark(wm.DATASET_INDEX_DAILY, d, wm.STATUS_OK, int(cnt))
            dates_ok += 1
            _log_progress("index_daily", "%s 入库 %d 条", d.isoformat(), int(cnt))
        else:
            wm.set_watermark(wm.DATASET_INDEX_DAILY, d, wm.STATUS_ERROR, 0, "empty")
            _log_progress("index_daily", "%s 无数据", d.isoformat())

    _log_done(
        "index_daily", "index_daily", fetched_rows,
        dates_ok=dates_ok, codes=len(codes), source="tushare.index_daily",
    )

    # 个股补洞：对窗口内行数不足的 index code 按 code 区间补数
    # overwrite/区间模式不触发（区间模式已显式覆盖目标日期）
    if (
        code is None
        and not overwrite
        and not range_mode
        and days >= int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
    ):
        try:
            fetched_rows += _backfill_thin_codes(
                conn, cursor, pro,
                daily_table="index_daily",
                basic_table="index_basic",
                api_method="index_daily",
                upsert_fn=lambda df: _upsert_index_daily(conn, df),
                job_label="index_daily",
                threshold_env="XSHARE_INDEX_DAILY_CODE_THRESHOLD",
                dataset=wm.DATASET_INDEX_DAILY,
            )
        except Exception as exc:
            logger.warning("指数日线补洞失败: %s", exc)

    return fetched_rows


# ─── etf_basic / fund_daily ──────────────────────────────────────────────────


def fetch_etf_basic(list_status: str = "L") -> pd.DataFrame:
    """拉取 ETF 基础信息（默认仅上市）。"""
    df = _pro_call(
        "etf_basic",
        list_status=list_status,
        fields=(
            "ts_code,csname,extname,index_code,index_name,exchange,"
            "mgr_name,etf_type,list_status,setup_date,list_date"
        ),
    )
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "code", "name", "extname", "index_code", "index_name", "exchange",
                "mgr_name", "etf_type", "list_status", "setup_date", "list_date",
            ]
        )
    out = df.rename(columns={"ts_code": "code", "csname": "name"})
    if "name" not in out.columns or out["name"].isna().all():
        if "extname" in out.columns:
            out["name"] = out["extname"]
    out["name"] = out["name"].fillna(out.get("extname", "")).astype(str)
    for col in ("setup_date", "list_date"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], format="%Y%m%d", errors="coerce").dt.date
    return out


def sync_etf_basic_to_db(force: bool = False) -> int:
    """从 Tushare 拉取 ETF 基础信息并写入 etf_basic。"""
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)
    _log_start("etf_basic", force=force, table="etf_basic")

    if not force:
        row = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM etf_basic"
        ).fetchone()
        count, last_update = row
        if count > 0 and last_update is not None:
            age_hours = (datetime.now() - last_update.replace(tzinfo=None)).total_seconds() / 3600
            if age_hours < 24:
                _log_skip("etf_basic", f"本地已有 {count} 条且 {age_hours:.1f}h 内已更新")
                return 0

    try:
        df = fetch_etf_basic()
        if df.empty:
            wm.set_watermark(wm.DATASET_ETF_BASIC, "ALL", wm.STATUS_ERROR, 0, "empty response")
            _log_skip("etf_basic", "上游返回空")
            return 0

        cols = [
            "code", "name", "extname", "index_code", "index_name", "exchange",
            "mgr_name", "etf_type", "list_status", "setup_date", "list_date",
        ]
        df_insert = df[[c for c in cols if c in df.columns]].copy()
        for c in cols:
            if c not in df_insert.columns:
                df_insert[c] = None

        conn.execute("DELETE FROM etf_basic")
        conn.register("_eb_df", df_insert)
        conn.execute("""
            INSERT INTO etf_basic (
                code, name, extname, index_code, index_name, exchange,
                mgr_name, etf_type, list_status, setup_date, list_date, updated_at
            )
            SELECT code, name, extname, index_code, index_name, exchange,
                   mgr_name, etf_type, list_status, setup_date, list_date, current_timestamp
            FROM _eb_df
        """)
        conn.unregister("_eb_df")
        wm.set_watermark(wm.DATASET_ETF_BASIC, "ALL", wm.STATUS_OK, len(df_insert))
        _log_done("etf_basic", "etf_basic", len(df_insert), source="tushare.etf_basic")
        return len(df_insert)
    except Exception as exc:
        wm.set_watermark(wm.DATASET_ETF_BASIC, "ALL", wm.STATUS_ERROR, error=str(exc))
        raise


def get_fund_daily_coverage(lookback_trading_days: int | None = None) -> dict:
    """统计本地 ETF 日线覆盖率。"""
    from xshare.data.db import get_conn, init_tables

    target = lookback_trading_days
    if target is None:
        target = int(os.environ.get("XSHARE_FUND_DAILY_MIN_TRADING_DAYS", "252") or "252")
    target = max(1, target)

    conn = get_conn()
    init_tables(conn)
    since = date.today() - timedelta(days=max(target * 2, 365))
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date)
        FROM fund_daily
        WHERE trade_date >= ?
        """,
        [since],
    ).fetchone()
    in_db = int(row[0] or 0)
    per_code = _per_code_coverage(
        conn, "fund_daily", "etf_basic", target, "XSHARE_FUND_DAILY_CODE_THRESHOLD",
    )
    return {
        "trading_days_in_db": in_db,
        "target_days": target,
        "missing_estimate": max(0, target - in_db),
        "oldest": str(row[1]) if row[1] else None,
        "newest": str(row[2]) if row[2] else None,
        "sufficient": in_db >= target and per_code["sufficient"],
        "per_code": per_code,
        "watermark_latest_ok": wm.latest_ok_key(wm.DATASET_FUND_DAILY),
        "watermark_ok_count": wm.summarize(wm.DATASET_FUND_DAILY).get("ok_count", 0),
        **_daily_sync_status(wm.DATASET_FUND_DAILY),
    }


def find_missing_fund_daily_dates(days: int = 252) -> list[date]:
    """期望最近 days 个交易日中，fund_daily watermark 尚未 ok 的日期。"""
    pro = None
    try:
        if os.environ.get("TUSHARE_TOKEN"):
            pro = _get_pro()
    except Exception:
        pro = None
    expected = _resolve_trade_days(date.today(), days, pro or type("P", (), {})())
    return wm.find_daily_gaps(expected, dataset=wm.DATASET_FUND_DAILY)


def sync_fund_daily_to_db(
    trade_date: str | None = None,
    days: int = 1,
    code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
) -> int:
    """按交易日批量拉取 ETF 日线（pro.fund_daily）写入 fund_daily。

    优先 ``trade_date=`` 全市场单日拉取；失败时回退按 etf_basic 代码循环。
    若单 code 在目标区间内缺失 >1 段，直接按一年区间拉取该 code，减少调用次数。

    日期范围二选一：``start_date``/``end_date`` 按区间补全，或 ``days``
    取最近 N 个交易日。``overwrite=True`` 时强制重拉覆盖已有数据。
    """
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    range_mode = bool(start_date or end_date)
    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()

    if range_mode:
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else cursor
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else cursor
        if start_d > end_d:
            raise ValueError(f"start_date({start_d}) 不能晚于 end_date({end_d})")
        trade_days = _resolve_trade_days_range(start_d, end_d, pro)
        days = len(trade_days)
    else:
        if days <= 0:
            return 0
        trade_days = _resolve_trade_days(cursor, days, pro)
    if not trade_days:
        _log_skip("fund_daily", "无交易日")
        return 0

    since = min(trade_days)
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM fund_daily WHERE trade_date >= ?", [since]
    ).fetchall()
    existing = {r[0] for r in rows}
    ok_wm = wm.ok_keys(wm.DATASET_FUND_DAILY, since=since)
    fetched_rows = 0
    skipped = 0
    _log_start(
        "fund_daily",
        table="fund_daily",
        days=days,
        trade_days=",".join(d.isoformat() for d in trade_days[:5])
        + ("..." if len(trade_days) > 5 else ""),
        dates=len(trade_days),
        overwrite=overwrite,
        range_mode=range_mode,
    )

    range_df = None
    if code and trade_days:
        # 单 code 缺口检测：若缺失 >1 段，直接拉一年区间覆盖，减少调用次数
        year_lookback = int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
        year_days = _resolve_trade_days(cursor, year_lookback, pro)
        year_have = _existing_dates_for_code(conn, "fund_daily", code, year_days)
        year_missing = _missing_segments_from_have(year_days, year_have)
        if len(year_missing) > 1:
            _log_progress(
                "fund_daily", "code=%s 缺失 %d 段，按一年区间拉取 %s..%s",
                code, len(year_missing),
                min(year_days).isoformat(), max(year_days).isoformat(),
            )
            df = _pro_call(
                "fund_daily", ts_code=code,
                start_date=min(year_days).strftime("%Y%m%d"),
                end_date=max(year_days).strftime("%Y%m%d"),
            )
            fetched_rows = _upsert_fund_daily(conn, df)
            for d in trade_days:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM fund_daily WHERE trade_date = ?", [d]
                ).fetchone()[0]
                if cnt and int(cnt) > 0:
                    wm.set_watermark(wm.DATASET_FUND_DAILY, d, wm.STATUS_OK, int(cnt))
            _log_done(
                "fund_daily", "fund_daily", fetched_rows,
                skipped=skipped, source="tushare.fund_daily.range",
            )
            return fetched_rows
        range_df = _pro_call(
            "fund_daily", ts_code=code,
            start_date=min(trade_days).strftime("%Y%m%d"),
            end_date=max(trade_days).strftime("%Y%m%d"),
        )
    for idx, d in enumerate(trade_days):
        yyyymmdd = d.strftime("%Y%m%d")
        iso = d.isoformat()

        # overwrite 模式不跳过已有日期，强制重拉覆盖
        if not overwrite and idx > 0 and d in existing:
            cnt_existing = conn.execute(
                "SELECT COUNT(*) FROM fund_daily WHERE trade_date = ?", [d]
            ).fetchone()[0]
            thin_threshold = 0
            try:
                n_etf = conn.execute("SELECT COUNT(*) FROM etf_basic").fetchone()[0]
                thin_threshold = int(n_etf * 0.95)
            except Exception:
                pass
            if thin_threshold <= 0 or cnt_existing >= thin_threshold:
                if iso not in ok_wm:
                    wm.set_watermark(wm.DATASET_FUND_DAILY, d, wm.STATUS_OK, int(cnt_existing or 0))
                skipped += 1
                _log_progress("fund_daily", "%s 跳过（已入库 %d 行）", iso, cnt_existing)
                continue
            else:
                _log_progress(
                    "fund_daily", "%s 稀疏日（仅 %d 行，阈值 %d）重新拉取", iso, cnt_existing, thin_threshold,
                )

        try:
            if range_df is not None:
                df = range_df[
                    range_df["trade_date"].astype(str).str.replace("-", "") == yyyymmdd
                ].copy()
            else:
                df = _pro_call("fund_daily", trade_date=yyyymmdd)
            mode = "trade_date"
            if df is None or df.empty:
                codes = [
                    r[0]
                    for r in conn.execute("SELECT code FROM etf_basic ORDER BY code").fetchall()
                ]
                if not codes:
                    sync_etf_basic_to_db(force=True)
                    codes = [
                        r[0]
                        for r in conn.execute("SELECT code FROM etf_basic ORDER BY code").fetchall()
                    ]
                frames = []
                for code in codes:
                    try:
                        one = _pro_call(
                            "fund_daily",
                            ts_code=code, start_date=yyyymmdd, end_date=yyyymmdd,
                        )
                        if one is not None and not one.empty:
                            frames.append(one)
                    except Exception as exc:
                        logger.debug("fund_daily %s %s 失败: %s", code, yyyymmdd, exc)
                df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                mode = "per_code"

            if df is None or df.empty:
                wm.set_watermark(wm.DATASET_FUND_DAILY, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("fund_daily", "%s 无数据", iso)
                continue

            n = _upsert_fund_daily(conn, df)
            fetched_rows += n
            wm.set_watermark(wm.DATASET_FUND_DAILY, d, wm.STATUS_OK, n)
            _log_progress(
                "fund_daily", "%s 入库 %d 条 mode=%s → fund_daily",
                iso, n, mode,
            )
        except Exception as exc:
            wm.set_watermark(wm.DATASET_FUND_DAILY, d, wm.STATUS_ERROR, error=str(exc))
            logger.warning("ETF 日线同步 %s 失败: %s", yyyymmdd, exc)
            raise

    _log_done(
        "fund_daily", "fund_daily", fetched_rows,
        skipped=skipped, source="tushare.fund_daily",
    )

    # 个股补洞：对窗口内行数不足的 ETF code 按 code 区间补数
    # overwrite/区间模式不触发（区间模式已显式覆盖目标日期）
    if (
        code is None
        and not overwrite
        and not range_mode
        and days >= int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
    ):
        try:
            fetched_rows += _backfill_thin_codes(
                conn, cursor, pro,
                daily_table="fund_daily",
                basic_table="etf_basic",
                api_method="fund_daily",
                upsert_fn=lambda df: _upsert_fund_daily(conn, df),
                job_label="fund_daily",
                threshold_env="XSHARE_FUND_DAILY_CODE_THRESHOLD",
                dataset=wm.DATASET_FUND_DAILY,
            )
        except Exception as exc:
            logger.warning("ETF 日线补洞失败: %s", exc)

    return fetched_rows


# ─── trade_cal ───────────────────────────────────────────────────────────────


def sync_trade_cal_to_db(years: int = 3) -> int:
    """同步近 years 年交易日历到本地 trade_cal 表。"""
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)
    _log_start("trade_cal", table="trade_cal", years=years)

    end = date.today() + timedelta(days=30)
    start = date.today() - timedelta(days=max(years, 1) * 365)
    try:
        cal = _pro_call(
            "trade_cal",
            exchange="",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except TypeError:
        cal = _pro_call(
            "trade_cal",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )

    if cal is None or cal.empty:
        wm.set_watermark(wm.DATASET_TRADE_CAL, "ALL", wm.STATUS_ERROR, 0, "empty")
        _log_skip("trade_cal", "上游返回空")
        return 0

    col = "cal_date" if "cal_date" in cal.columns else "trade_date"
    cal = cal.copy()
    cal["cal_date"] = pd.to_datetime(cal[col], format="%Y%m%d", errors="coerce").dt.date
    if "is_open" in cal.columns:
        cal["is_open"] = cal["is_open"].astype(str).isin(("1", "True", "true"))
    else:
        cal["is_open"] = True
    if "pretrade_date" in cal.columns:
        cal["pretrade_date"] = pd.to_datetime(cal["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
    else:
        cal["pretrade_date"] = None

    df_insert = cal[["cal_date", "is_open", "pretrade_date"]].dropna(subset=["cal_date"])
    open_days = int(df_insert["is_open"].sum()) if "is_open" in df_insert.columns else 0
    conn.register("_tc_df", df_insert)
    conn.execute(
        """
        INSERT INTO trade_cal (cal_date, is_open, pretrade_date, updated_at)
        SELECT cal_date, is_open, pretrade_date, now() FROM _tc_df
        ON CONFLICT (cal_date) DO UPDATE SET
            is_open=EXCLUDED.is_open,
            pretrade_date=EXCLUDED.pretrade_date,
            updated_at=now()
        """
    )
    conn.unregister("_tc_df")
    wm.set_watermark(wm.DATASET_TRADE_CAL, "ALL", wm.STATUS_OK, len(df_insert))
    _log_done(
        "trade_cal", "trade_cal", len(df_insert),
        open_days=open_days, range=f"{start}..{end}", source="tushare.trade_cal",
    )
    return len(df_insert)


def is_trade_day_local(day: date) -> bool | None:
    """查本地 trade_cal；无记录返回 None（调用方回退）。"""
    from xshare.data.db import get_conn, init_tables

    try:
        conn = get_conn()
        init_tables(conn)
        row = conn.execute(
            "SELECT is_open FROM trade_cal WHERE cal_date = ?", [day]
        ).fetchone()
        if row is None:
            return None
        return bool(row[0])
    except Exception:
        return None


def list_open_trade_days(start: date, end: date) -> list[date]:
    """从本地日历取开市日；本地无数据时返回空列表。"""
    from xshare.data.db import get_conn, init_tables

    conn = get_conn()
    init_tables(conn)
    rows = conn.execute(
        """
        SELECT cal_date FROM trade_cal
        WHERE cal_date BETWEEN ? AND ? AND is_open = TRUE
        ORDER BY cal_date
        """,
        [start, end],
    ).fetchall()
    return [r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0])[:10]) for r in rows]

def _latest_trade_date_local() -> date | None:
    """本地日历中 ≤ today 的最近开市日；无日历返回 None。"""
    today = date.today()
    days = list_open_trade_days(today - timedelta(days=15), today)
    return days[-1] if days else None


def _daily_sync_status(dataset: str) -> dict:
    """判断日线数据集最新交易日的同步状态。

    逻辑：取本地日历最近交易日，查该 dataset 在该日的 watermark。
    status=ok → 'synced'；有 watermark 但非 ok → 'error'；
    无日历或无 watermark → 'unsynced'。
    """
    ltd = _latest_trade_date_local()
    if ltd is None:
        return {"sync_status": "unsynced", "latest_trade_date": None}
    w = wm.get_watermark(dataset, ltd)
    if w and w.get("status") == wm.STATUS_OK:
        return {"sync_status": "synced", "latest_trade_date": str(ltd)}
    if w:
        return {"sync_status": "error", "latest_trade_date": str(ltd)}
    return {"sync_status": "unsynced", "latest_trade_date": str(ltd)}






# ─── daily coverage / history fetch ──────────────────────────────────────────


def fetch_daily(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取单票日线行情（force_refresh 路径，经限速）。"""
    df = _pro_call("daily", ts_code=code, start_date=start_date, end_date=end_date)
    df = df.rename(columns={
        "ts_code": "code", "trade_date": "trade_date",
        "vol": "volume", "amount": "amount",
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df[["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]]


def _per_code_coverage(
    conn,
    daily_table: str,
    basic_table: str,
    target: int,
    threshold_env: str,
) -> dict:
    """计算某日线表的 per-code 覆盖维度。

    用于 stock_daily / index_daily / fund_daily，逻辑统一：
    最近 target 个交易日窗口内，basic 表里的 code 数 vs 各 code 在
    daily 表里的行数，算出达标/不足 code 数与整体行数比率。
    """
    threshold = float(os.environ.get(threshold_env, "0.95") or "0.95")
    per_code: dict = {
        "total": 0,
        "sufficient_count": 0,
        "under": 0,
        "rows_in_window": 0,
        "total_expected": 0,
        "ratio": 0.0,
        "threshold": threshold,
        "sufficient": True,
        # 上市时间维度：上市未满窗口期的 code 不应被判为"不足"
        "listed_in_window": 0,
        "seasoned_total": 0,
        "window_start": None,
    }
    try:
        trade_days = _resolve_trade_days(date.today(), target, None)
        if not trade_days:
            return per_code
        window_start = min(trade_days)
        window_end = max(trade_days)
        window_set = set(trade_days)
        n_dates = len(trade_days)
        per_code["window_start"] = str(window_start)
        # basic 表所有 code + 上市日（用于按实际上市日折算次新的应有天数）
        basic_rows = conn.execute(
            f"SELECT code, list_date FROM {basic_table}"
        ).fetchall()
        n_codes = len(basic_rows)
        per_code["total"] = n_codes
        list_date_map = {r[0]: r[1] for r in basic_rows}
        listed_in_window = sum(
            1 for ld in list_date_map.values()
            if ld is not None and ld >= window_start
        )
        per_code["listed_in_window"] = listed_in_window
        seasoned_codes = [
            (c, ld) for c, ld in list_date_map.items()
            if ld is None or ld < window_start
        ]
        seasoned_total = len(seasoned_codes)
        per_code["seasoned_total"] = seasoned_total
        per_code["total_expected"] = seasoned_total * n_dates
        if seasoned_total > 0 and n_dates > 0:
            # 口径对齐：仅统计 basic 表内、且上市满窗口期的 seasoned code。
            # 按 code 逐只算 expected（次新按实际上市日折算），实时算 actual。
            seasoned_code_list = [c for c, _ in seasoned_codes]
            code_to_cnt = {
                r[0]: int(r[1] or 0)
                for r in conn.execute(
                    f"""
                    SELECT code, COUNT(*) AS n
                    FROM {daily_table}
                    WHERE trade_date >= ? AND trade_date <= ?
                      AND code IN (SELECT unnest(?))
                    GROUP BY code
                    """,
                    [window_start, window_end, seasoned_code_list],
                ).fetchall()
            }
            rows_in_window = sum(code_to_cnt.values())
            per_code["rows_in_window"] = rows_in_window
            total_expected = 0
            enough = 0
            min_rows = max(1, int(threshold * n_dates))
            for code, ld in seasoned_codes:
                if ld is None or ld <= window_start:
                    expected = n_dates
                else:
                    expected = sum(1 for d in window_set if d >= ld)
                expected = max(1, expected)
                total_expected += expected
                if code_to_cnt.get(code, 0) >= min_rows:
                    enough += 1
            per_code["total_expected"] = total_expected
            per_code["ratio"] = (
                round(rows_in_window / total_expected, 4) if total_expected else 0.0
            )
            per_code["sufficient_count"] = enough
            per_code["under"] = max(0, seasoned_total - enough)
            per_code["sufficient"] = (
                per_code["ratio"] >= threshold and per_code["under"] == 0
            )
    except Exception as exc:
        logger.debug("%s per-code 覆盖统计失败: %s", daily_table, exc)
    return per_code


def get_daily_coverage(lookback_trading_days: int | None = None) -> dict:
    """统计本地日线覆盖率。

    同时衡量两个维度：
    1. 市场维度：最近窗口内 distinct trade_date 数（旧逻辑，保留兼容）
    2. 个股维度：最近 target 个交易日中，每只在市股票应有的行数 vs 实际行数

    ``sufficient`` 仅当两个维度都达标才为 True，避免"全市场日期够、但个股
    行数普遍不足"被误判为满足。
    """
    from xshare.data.db import get_conn, init_tables

    target = lookback_trading_days
    if target is None:
        target = int(os.environ.get("XSHARE_DAILY_MIN_TRADING_DAYS", "252") or "252")
    target = max(1, target)

    conn = get_conn()
    init_tables(conn)
    since = date.today() - timedelta(days=max(target * 2, 365))
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date)
        FROM stock_daily
        WHERE trade_date >= ?
        """,
        [since],
    ).fetchone()
    in_db = int(row[0] or 0)
    oldest = str(row[1]) if row[1] else None
    newest = str(row[2]) if row[2] else None
    missing = max(0, target - in_db)
    latest_wm = wm.latest_ok_key(wm.DATASET_DAILY)

    per_stock = _per_code_coverage(
        conn, "stock_daily", "stock_basic", target, "XSHARE_DAILY_STOCK_THRESHOLD",
    )

    return {
        "trading_days_in_db": in_db,
        "target_days": target,
        "missing_estimate": missing,
        "oldest": oldest,
        "newest": newest,
        "sufficient": in_db >= target and per_stock["sufficient"],
        "per_stock": per_stock,
        "watermark_latest_ok": latest_wm,
        "watermark_ok_count": wm.summarize(wm.DATASET_DAILY).get("ok_count", 0),
        **_daily_sync_status(wm.DATASET_DAILY),
    }


def find_code_missing_segments(
    code: str,
    table: str,
    expected_dates: list[date],
) -> list[tuple[date, date]]:
    """检测某 code 在给定表中缺失的连续时间段。

    返回缺失段列表 [(start, end), ...]，按时间升序。若缺失段数 > 1，
    调用方通常应直接按整体区间拉取（例如一年）而非逐段拉取。
    """
    from xshare.data.db import get_conn

    if not expected_dates:
        return []
    expected = sorted(expected_dates)
    have: set[date] = set()
    try:
        conn = get_conn()
        rows = conn.execute(
            f"SELECT trade_date FROM {table} WHERE code = ? AND trade_date >= ? AND trade_date <= ?",
            [code, expected[0], expected[-1]],
        ).fetchall()
        for r in rows:
            d = r[0]
            if isinstance(d, datetime):
                d = d.date()
            elif not isinstance(d, date):
                d = date.fromisoformat(str(d)[:10])
            have.add(d)
    except Exception as exc:
        logger.debug("find_code_missing_segments %s %s 查询失败: %s", table, code, exc)
        # 查询失败视为全缺（一段）
        return [(expected[0], expected[-1])]

    segments: list[tuple[date, date]] = []
    seg_start: date | None = None
    seg_end: date | None = None
    for d in expected:
        if d not in have:
            if seg_start is None:
                seg_start = d
            seg_end = d
        else:
            if seg_start is not None:
                segments.append((seg_start, seg_end))  # noqa: F821
                seg_start = None
    if seg_start is not None:
        segments.append((seg_start, seg_end))  # noqa: F821
    return segments


def _missing_segments_from_have(
    expected: list[date],
    have: set[date],
) -> list[tuple[date, date]]:
    """根据 expected 交易日和已入库日期集合，计算缺失的连续段。"""
    if not expected:
        return []
    segments: list[tuple[date, date]] = []
    seg_start: date | None = None
    seg_end: date | None = None
    for d in sorted(expected):
        if d not in have:
            if seg_start is None:
                seg_start = d
            seg_end = d
        else:
            if seg_start is not None:
                segments.append((seg_start, seg_end))  # noqa: F821
                seg_start = None
    if seg_start is not None:
        segments.append((seg_start, seg_end))  # noqa: F821
    return segments


def _existing_dates_for_code(conn, table: str, code: str, expected: list[date]) -> set[date]:
    """返回某 code 在给定 expected 区间内已入库的 trade_date 集合。"""
    if not expected:
        return set()
    rows = conn.execute(
        f"SELECT trade_date FROM {table} WHERE code = ? AND trade_date >= ? AND trade_date <= ?",
        [code, min(expected), max(expected)],
    ).fetchall()
    out: set[date] = set()
    for r in rows:
        d = r[0]
        if isinstance(d, datetime):
            d = d.date()
        elif not isinstance(d, date):
            d = date.fromisoformat(str(d)[:10])
        out.add(d)
    return out


def _upsert_daily_table(conn, df: pd.DataFrame, table: str, asset_type: str, register_name: str) -> int:
    """把 fund/index 日线 DataFrame 写入对应表（upsert），返回写入行数。

    fund_daily 与 index_daily 表结构完全一致，统一实现消除重复。
    """
    if df is None or df.empty:
        return 0
    df = df.rename(columns={"ts_code": "code", "vol": "volume"})
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
    cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    df_insert = df[[c for c in cols if c in df.columns]].copy()
    if "pct_chg" not in df_insert.columns:
        df_insert["pct_chg"] = None
    conn.register(register_name, df_insert)
    conn.execute(
        f"""
        INSERT INTO {table} (code, trade_date, open, high, low, close, volume, amount, pct_chg)
        SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg FROM {register_name}
        ON CONFLICT (code, trade_date) DO UPDATE SET
            open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
            close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount,
            pct_chg=EXCLUDED.pct_chg
        """
    )
    conn.unregister(register_name)
    _refresh_code_meta(conn, table, asset_type, df_insert["code"].unique())
    return len(df_insert)


def _upsert_fund_daily(conn, df: pd.DataFrame) -> int:
    """把 fund_daily DataFrame 写入 fund_daily 表（upsert），返回写入行数。"""
    return _upsert_daily_table(conn, df, "fund_daily", "etf", "_fd_df")


def _upsert_index_daily(conn, df: pd.DataFrame) -> int:
    """把 index_daily DataFrame 写入 index_daily 表（upsert），返回写入行数。"""
    return _upsert_daily_table(conn, df, "index_daily", "index", "_id_df")


def _refresh_code_meta(conn, daily_table: str, asset_type: str, codes) -> None:
    """增量刷新 code_meta：只重算给定 code 集合的元数据。

    window_count / sufficient 基于"最近 252 交易日"窗口。对每个 code，
    expected = 该 code 在窗口内自上市日起应有的交易日数（次新按实际折算）。
    任何异常都吞掉，不影响主写入。
    """
    try:
        code_list = [c for c in codes if c]
        if not code_list:
            return
        target = int(os.environ.get("XSHARE_DAILY_MIN_TRADING_DAYS", "252") or "252")
        threshold = {
            "stock": float(os.environ.get("XSHARE_DAILY_STOCK_THRESHOLD", "0.95") or "0.95"),
            "index": float(os.environ.get("XSHARE_INDEX_DAILY_CODE_THRESHOLD", "0.95") or "0.95"),
            "etf": float(os.environ.get("XSHARE_FUND_DAILY_CODE_THRESHOLD", "0.95") or "0.95"),
        }.get(asset_type, 0.95)
        basic_table = {"stock": "stock_basic", "index": "index_basic", "etf": "etf_basic"}[asset_type]
        trade_days = _resolve_trade_days(date.today(), target, None)
        if not trade_days:
            return
        window_start = min(trade_days)
        window_end = max(trade_days)
        window_set = set(trade_days)
        n_window = len(trade_days)

        # 全量统计 + 窗口内行数
        agg = conn.execute(
            f"""
            SELECT d.code,
                   COUNT(*) AS data_count,
                   MIN(d.trade_date) AS first_trade_date,
                   MAX(d.trade_date) AS latest_trade_date,
                   SUM(CASE WHEN d.trade_date >= ? AND d.trade_date <= ? THEN 1 ELSE 0 END) AS window_count
            FROM {daily_table} d
            WHERE d.code IN (SELECT unnest(?))
            GROUP BY d.code
            """,
            [window_start, window_end, code_list],
        ).fetchall()
        # 上市日期
        list_dates = {
            r[0]: r[1]
            for r in conn.execute(
                f"SELECT code, list_date FROM {basic_table} WHERE code IN (SELECT unnest(?))",
                [code_list],
            ).fetchall()
        }
        rows_to_upsert = []
        for code, data_count, first_td, latest_td, win_cnt in agg:
            list_date = list_dates.get(code)
            # 应有窗口交易日数：上市早于窗口起点 → n_window；否则取窗口内 >= 上市日的交易日数
            if list_date is None or list_date <= window_start:
                expected = n_window
            else:
                expected = sum(1 for d in window_set if d >= list_date)
            expected = max(1, expected)
            win_cnt = int(win_cnt or 0)
            has_one_year = (
                first_td is not None and latest_td is not None
                and (latest_td - first_td).days >= 365
            )
            suff = win_cnt >= int(threshold * expected)
            rows_to_upsert.append((
                code, asset_type, int(data_count or 0),
                first_td, latest_td, has_one_year,
                win_cnt, expected, suff,
            ))
        if rows_to_upsert:
            cm_df = pd.DataFrame(
                rows_to_upsert,
                columns=[
                    "code", "asset_type", "data_count", "first_trade_date",
                    "latest_trade_date", "has_one_year_data",
                    "window_count", "window_expected", "sufficient",
                ],
            )
            conn.register("_cm_df", cm_df)
            conn.execute(
                """
                INSERT INTO code_meta
                    (code, asset_type, data_count, first_trade_date, latest_trade_date,
                     has_one_year_data, window_count, window_expected, sufficient)
                SELECT code, asset_type, data_count, first_trade_date, latest_trade_date,
                       has_one_year_data, window_count, window_expected, sufficient
                FROM _cm_df
                ON CONFLICT (code) DO UPDATE SET
                    asset_type=EXCLUDED.asset_type,
                    data_count=EXCLUDED.data_count,
                    first_trade_date=EXCLUDED.first_trade_date,
                    latest_trade_date=EXCLUDED.latest_trade_date,
                    has_one_year_data=EXCLUDED.has_one_year_data,
                    window_count=EXCLUDED.window_count,
                    window_expected=EXCLUDED.window_expected,
                    sufficient=EXCLUDED.sufficient
                """
            )
            conn.unregister("_cm_df")
        # 同步任务可能拉到 basic 表里没有的 code（已退市/未收录），
        # 这些 code_meta 行予以保留（asset_type 来自 daily 上下文），
        # 不影响覆盖率（覆盖率只 JOIN basic 表）。
    except Exception as exc:
        logger.debug("code_meta 刷新失败 (%s): %s", daily_table, exc)


def rebuild_code_meta_all(conn) -> int:
    """全量重算 code_meta（建表回填 / 周期校准用）。返回刷新行数。"""
    total = 0
    for daily_table, asset_type in (
        ("stock_daily", "stock"),
        ("fund_daily", "etf"),
        ("index_daily", "index"),
    ):
        try:
            codes = [r[0] for r in conn.execute(
                f"SELECT DISTINCT code FROM {daily_table}"
            ).fetchall()]
            _refresh_code_meta(conn, daily_table, asset_type, codes)
            total += len(codes)
        except Exception as exc:
            logger.debug("code_meta 全量重算 %s 失败: %s", daily_table, exc)
    return total


def _upsert_stock_daily(conn, df: pd.DataFrame) -> int:
    """把 stock daily DataFrame 写入 stock_daily 表（upsert），返回写入行数。"""
    if df is None or df.empty:
        return 0
    df = df.rename(columns={"ts_code": "code", "vol": "volume"})
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
    cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    df_insert = df[[c for c in cols if c in df.columns]]
    conn.register("_sd_df", df_insert)
    conn.execute(
        "INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount) "
        "SELECT code, trade_date, open, high, low, close, volume, amount FROM _sd_df "
        "ON CONFLICT (code, trade_date) DO UPDATE SET "
        "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
        "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
    )
    conn.unregister("_sd_df")
    _refresh_code_meta(conn, "stock_daily", "stock", df_insert["code"].unique())
    return len(df_insert)


def _resolve_trade_days(cursor: date, days: int, pro) -> list[date]:
    """解析最近 days 个交易日（优先本地日历 → API trade_cal → weekday）。"""
    local = list_open_trade_days(cursor - timedelta(days=max(days * 3, 90)), cursor)
    if local:
        return sorted(local, reverse=True)[:days]

    trade_days: list[date] = []
    if hasattr(pro, "trade_cal"):
        lookback_days = max(days * 3, 90)
        start = (cursor - timedelta(days=lookback_days)).strftime("%Y%m%d")
        end = cursor.strftime("%Y%m%d")
        try:
            cal = _pro_call("trade_cal", exchange="", start_date=start, end_date=end, is_open="1")
        except TypeError:
            cal = _pro_call("trade_cal", start_date=start, end_date=end)

        if cal is not None and not cal.empty:
            col = "cal_date" if "cal_date" in cal.columns else "trade_date" if "trade_date" in cal.columns else None
            if col:
                parsed = pd.to_datetime(cal[col], format="%Y%m%d", errors="coerce").dropna().dt.date
                trade_days = sorted({d for d in parsed if d <= cursor}, reverse=True)[:days]

    if not trade_days:
        d = cursor
        max_lookback = days * 7 + 15
        looked = 0
        while len(trade_days) < days and looked < max_lookback:
            if d.weekday() < 5:
                trade_days.append(d)
            d -= timedelta(days=1)
            looked += 1
    return trade_days


def _resolve_trade_days_range(
    start: date, end: date, pro
) -> list[date]:
    """解析 [start, end] 区间内的交易日（升序）。

    优先本地 trade_cal；为空时回退到 _resolve_trade_days 取区间并按端点过滤，
    再退化为工作日近似。用于一次性按时间段补全（start_date/end_date）。
    """
    local = list_open_trade_days(start, end)
    if local:
        return local
    # 本地日历为空：用 _resolve_trade_days 拉足够大的窗口再过滤到区间内
    span_days = max((end - start).days, 1)
    window = _resolve_trade_days(end, span_days * 2 + 10, pro)
    return sorted(d for d in window if start <= d <= end)


def find_missing_daily_dates(days: int = 252) -> list[date]:
    """期望最近 days 个交易日中，watermark 尚未 ok 的日期。"""
    pro = None
    try:
        if os.environ.get("TUSHARE_TOKEN"):
            pro = _get_pro()
    except Exception:
        pro = None
    expected = _resolve_trade_days(date.today(), days, pro or type("P", (), {})())
    # 也把 stock_daily 已有但无 watermark 的日子补上 watermark（不强制重拉）
    return wm.find_daily_gaps(expected)


def sync_stock_daily_to_db(
    trade_date: str | None = None,
    days: int = 1,
    code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
) -> int:
    """从 Tushare 批量拉取日线行情并写入本地 DuckDB，同时更新 watermark。

    日期范围二选一：
    - ``start_date``/``end_date``（YYYY-MM-DD）：按该区间内的交易日补全，
      适用于一次性补全。``overwrite=True`` 时强制重拉覆盖已有数据。
    - ``days``：最近 N 个交易日（默认 1），用于增量同步。
    """
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    range_mode = bool(start_date or end_date)
    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()
    fetched_rows = 0

    if range_mode:
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else cursor
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else cursor
        if start_d > end_d:
            raise ValueError(f"start_date({start_d}) 不能晚于 end_date({end_d})")
        trade_days = _resolve_trade_days_range(start_d, end_d, pro)
        days = len(trade_days)
    else:
        if days <= 0:
            return 0
        trade_days = _resolve_trade_days(cursor, days, pro)

    since = min(trade_days) if trade_days else cursor
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date >= ?", [since]
    ).fetchall()
    existing = {r[0] for r in rows}
    ok_wm = wm.ok_keys(wm.DATASET_DAILY, since=since)
    skipped = 0
    _log_start(
        "daily",
        table="stock_daily",
        days=days,
        dates=len(trade_days),
        trade_days=",".join(d.isoformat() for d in trade_days[:5])
        + ("..." if len(trade_days) > 5 else ""),
        overwrite=overwrite,
        range_mode=range_mode,
    )

    range_df = None
    if code and trade_days:
        # 单 code 缺口检测：若缺失 >1 段，直接拉一年区间覆盖，减少调用次数
        year_lookback = int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
        year_days = _resolve_trade_days(cursor, year_lookback, pro)
        year_have = _existing_dates_for_code(conn, "stock_daily", code, year_days)
        year_missing = _missing_segments_from_have(year_days, year_have)
        if len(year_missing) > 1:
            _log_progress(
                "daily", "code=%s 缺失 %d 段，按一年区间拉取 %s..%s",
                code, len(year_missing),
                min(year_days).isoformat(), max(year_days).isoformat(),
            )
            df = _pro_call(
                "daily", ts_code=code,
                start_date=min(year_days).strftime("%Y%m%d"),
                end_date=max(year_days).strftime("%Y%m%d"),
            )
            fetched_rows = _upsert_stock_daily(conn, df)
            for d in trade_days:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM stock_daily WHERE trade_date = ?", [d]
                ).fetchone()[0]
                if cnt and int(cnt) > 0:
                    wm.set_watermark(wm.DATASET_DAILY, d, wm.STATUS_OK, int(cnt))
            _log_done(
                "daily", "stock_daily", fetched_rows,
                skipped=skipped, source="tushare.daily.range",
            )
            return fetched_rows
        range_df = _pro_call(
            "daily", ts_code=code,
            start_date=min(trade_days).strftime("%Y%m%d"),
            end_date=max(trade_days).strftime("%Y%m%d"),
        )
    for idx, d in enumerate(trade_days):
        yyyymmdd = d.strftime("%Y%m%d")
        iso = d.isoformat()

        # 最新交易日强制刷新；已入库且行数充足的日期跳过。
        # 行数不足（稀疏日，如停牌/部分返回）的日期不跳过，重新拉全市场补齐。
        # overwrite=True（一次性补全覆盖模式）时一律不跳过，强制重拉。
        if not overwrite and idx > 0 and d in existing:
            cnt_existing = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE trade_date = ?", [d]
            ).fetchone()[0]
            # 满数据阈值：在市股票数 × 安全系数；低于此视为稀疏日需重拉
            thin_threshold = 0
            try:
                n_stocks = conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
                thin_threshold = int(n_stocks * 0.95)
            except Exception:
                pass
            if thin_threshold <= 0 or cnt_existing >= thin_threshold:
                if iso not in ok_wm:
                    wm.set_watermark(wm.DATASET_DAILY, d, wm.STATUS_OK, int(cnt_existing or 0))
                skipped += 1
                _log_progress("daily", "%s 跳过（已入库 %d 行）", iso, cnt_existing)
                continue
            else:
                _log_progress(
                    "daily", "%s 稀疏日（仅 %d 行，阈值 %d）重新拉取", iso, cnt_existing, thin_threshold,
                )

        try:
            if range_df is not None:
                df = range_df[
                    range_df["trade_date"].astype(str).str.replace("-", "") == yyyymmdd
                ].copy()
            else:
                df = _pro_call("daily", trade_date=yyyymmdd)
            if df is None or df.empty:
                wm.set_watermark(wm.DATASET_DAILY, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("daily", "%s 无数据", iso)
                continue

            n = _upsert_stock_daily(conn, df)
            fetched_rows += n
            wm.set_watermark(wm.DATASET_DAILY, d, wm.STATUS_OK, n)
            _log_progress(
                "daily", "%s 入库 %d 条 → stock_daily", iso, n,
            )
        except Exception as exc:
            wm.set_watermark(wm.DATASET_DAILY, d, wm.STATUS_ERROR, error=str(exc))
            logger.warning("日线同步 %s 失败: %s", yyyymmdd, exc)
            raise

    _log_done(
        "daily", "stock_daily", fetched_rows,
        skipped=skipped, source="tushare.daily",
    )

    # ── 个股补洞：market-wide 路径只按日期拉全市场，无法发现/修复
    #    某只股票在"已有日期"上的缺口（如停牌导致某天 Tushare daily
    #    返回不含该 code）。此处对行数不足的股票按 code 区间补数。
    #    仅在 backfill 任务（days 较大）时执行，日常增量不触发以免每天扫全市场。
    #    overwrite/区间模式不触发（区间模式已显式覆盖目标日期）。
    if (
        code is None
        and not overwrite
        and not range_mode
        and days >= int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
    ):
        try:
            fetched_rows += _backfill_thin_codes(
                conn, cursor, pro,
                daily_table="stock_daily",
                basic_table="stock_basic",
                api_method="daily",
                upsert_fn=lambda df: _upsert_stock_daily(conn, df),
                job_label="daily",
                threshold_env="XSHARE_DAILY_STOCK_THRESHOLD",
                dataset=wm.DATASET_DAILY,
            )
        except Exception as exc:
            logger.warning("日线个股补洞失败: %s", exc)

    return fetched_rows


def _backfill_thin_codes(
    conn,
    cursor: date,
    pro,
    *,
    daily_table: str,
    basic_table: str,
    api_method: str,
    upsert_fn,
    job_label: str,
    threshold_env: str,
    dataset: str,
) -> int:
    """对最近一年窗口内行数不足的 code，按 code 区间补数（通用版）。

    用于 stock_daily / index_daily / fund_daily。判定：某 code 在窗口内
    行数 < threshold * n_dates 或完全无数据。补法：多 code 批量区间拉取，
    失败降级逐只。受全局限速保护；单 code 失败不影响其他 code。
    """
    lookback = int(os.environ.get("XSHARE_DAILY_BACKFILL_DAYS", "252") or "252")
    lookback_days = _resolve_trade_days(cursor, lookback, pro)
    if not lookback_days:
        return 0
    n_dates = len(lookback_days)
    threshold = float(os.environ.get(threshold_env, "0.95") or "0.95")
    min_rows = max(1, int(threshold * n_dates))
    window_start = min(lookback_days)
    window_end = max(lookback_days)

    # basic 表 code 集合（list_date 可能缺失，用窗口末日过滤有意义的）
    try:
        rows = conn.execute(
            f"SELECT code FROM {basic_table} "
            f"WHERE list_date IS NULL OR list_date <= ?",
            [window_end],
        ).fetchall()
    except Exception as exc:
        logger.debug("%s 补洞读取 %s 失败: %s", job_label, basic_table, exc)
        return 0
    all_codes = {r[0] for r in rows}
    if not all_codes:
        return 0

    # 行数不足的 code（窗口内行数 < min_rows）
    thin = conn.execute(
        f"""
        SELECT code, COUNT(*) AS n
        FROM {daily_table}
        WHERE trade_date >= ? AND trade_date <= ?
        GROUP BY code
        HAVING n < ?
        """,
        [window_start, window_end, min_rows],
    ).fetchall()
    thin_codes = {r[0] for r in thin if r[0] in all_codes}

    # 完全没有数据的 code（窗口内 0 行）
    have_codes = conn.execute(
        f"""
        SELECT DISTINCT code FROM {daily_table}
        WHERE trade_date >= ? AND trade_date <= ?
        """,
        [window_start, window_end],
    ).fetchall()
    have_set = {r[0] for r in have_codes}
    missing_codes = all_codes - have_set
    target_codes = sorted(thin_codes | missing_codes)

    if not target_codes:
        return 0

    start_str = min(lookback_days).strftime("%Y%m%d")
    end_str = max(lookback_days).strftime("%Y%m%d")

    _log_progress(
        job_label, "补洞：%d 个 code 行数不足（阈值 %d/%d），按区间 %s..%s 补数",
        len(target_codes), min_rows, n_dates, start_str, end_str,
    )

    total = 0
    # 多 code 批量：ts_code 逗号拼接，单次 ≤6000 行；失败降级逐只
    total, _fallback = _fetch_multi_codes_batched(
        api_method, target_codes, start_str, end_str, lookback_days,
        upsert_fn, job_label,
    )

    if total:
        # 补数后刷新窗口内各日期 watermark 的 row_count
        for d in lookback_days:
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM {daily_table} WHERE trade_date = ?", [d]
            ).fetchone()[0]
            wm.set_watermark(dataset, d, wm.STATUS_OK, int(cnt or 0))
        _log_progress(job_label, "补洞完成，共补 %d 行", total)
    return total


# ─── daily_basic ─────────────────────────────────────────────────────────────


def sync_daily_basic_to_db(trade_date: str | None = None, days: int = 1) -> int:
    """按交易日批量同步每日指标到 stock_daily_basic。"""
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()
    trade_days = _resolve_trade_days(cursor, days, pro)
    fetched = 0
    skipped = 0
    _log_start("daily_basic", table="stock_daily_basic", days=days, dates=len(trade_days))

    for idx, d in enumerate(trade_days):
        iso = d.isoformat()
        if idx > 0 and iso in wm.ok_keys(wm.DATASET_DAILY_BASIC, since=d):
            skipped += 1
            _log_progress("daily_basic", "%s 跳过（水位已 ok）", iso)
            continue
        try:
            df = _pro_call(
                "daily_basic",
                trade_date=d.strftime("%Y%m%d"),
                fields="ts_code,trade_date,pe,pb,ps,total_mv,circ_mv,turnover_rate",
            )
            if df is None or df.empty:
                wm.set_watermark(wm.DATASET_DAILY_BASIC, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("daily_basic", "%s 无数据", iso)
                continue
            df = df.rename(columns={"ts_code": "code"})
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
            cols = ["code", "trade_date", "pe", "pb", "ps", "total_mv", "circ_mv", "turnover_rate"]
            df_insert = df[[c for c in cols if c in df.columns]]
            conn.register("_db_df", df_insert)
            conn.execute(
                """
                INSERT INTO stock_daily_basic
                    (code, trade_date, pe, pb, ps, total_mv, circ_mv, turnover_rate)
                SELECT code, trade_date, pe, pb, ps, total_mv, circ_mv, turnover_rate FROM _db_df
                ON CONFLICT (code, trade_date) DO UPDATE SET
                    pe=EXCLUDED.pe, pb=EXCLUDED.pb, ps=EXCLUDED.ps,
                    total_mv=EXCLUDED.total_mv, circ_mv=EXCLUDED.circ_mv,
                    turnover_rate=EXCLUDED.turnover_rate
                """
            )
            conn.unregister("_db_df")
            fetched += len(df_insert)
            wm.set_watermark(wm.DATASET_DAILY_BASIC, d, wm.STATUS_OK, len(df_insert))
            _log_progress(
                "daily_basic", "%s 入库 %d 条 → stock_daily_basic", iso, len(df_insert),
            )
        except Exception as exc:
            wm.set_watermark(wm.DATASET_DAILY_BASIC, d, wm.STATUS_ERROR, error=str(exc))
            raise
    _log_done(
        "daily_basic", "stock_daily_basic", fetched,
        skipped=skipped, source="tushare.daily_basic",
    )
    return fetched


# ─── finance ─────────────────────────────────────────────────────────────────


def fetch_financial_indicators(code: str) -> pd.DataFrame:
    """获取财务指标"""
    df = _pro_call(
        "fina_indicator",
        ts_code=code,
        fields="ts_code,end_date,roe,revenue_ps,profit_to_gr,q_roe,eps,bps,or_yoy,netprofit_yoy",
    )
    df = df.rename(columns={"ts_code": "code"})
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
    return df


def sync_finance_to_db(limit: int | None = None, force: bool = False) -> int:
    """按 stock_basic 列表分片同步财务指标；支持 watermark 断点。"""
    from xshare.data.db import get_conn, init_tables
    from xshare.data.provider import ProviderManager

    conn = get_conn()
    init_tables(conn)
    max_n = limit or int(os.environ.get("XSHARE_FINANCE_SYNC_LIMIT", "200") or "200")

    codes = [
        r[0]
        for r in conn.execute(
            "SELECT code FROM stock_basic ORDER BY code LIMIT ?", [max_n * 3]
        ).fetchall()
    ]
    if not codes:
        _log_skip("finance", "本地无 stock_basic")
        return 0

    _log_start("finance", table="stock_finance", limit=max_n, candidates=len(codes), force=force)
    synced = 0
    rows_total = 0
    for code in codes:
        if synced >= max_n:
            break
        if not force:
            existing = wm.get_watermark(wm.DATASET_FINANCE, code)
            if existing and existing["status"] == wm.STATUS_OK:
                # 7 天内成功过则跳过
                ts = existing.get("last_success_at")
                if ts:
                    try:
                        age = (datetime.now() - datetime.fromisoformat(str(ts)[:19])).days
                        if age < 7:
                            continue
                    except ValueError:
                        pass
        try:
            df = fetch_financial_indicators(code)
            if df.empty:
                wm.set_watermark(wm.DATASET_FINANCE, code, wm.STATUS_ERROR, 0, "empty")
                continue
            # 映射到 stock_finance schema
            out = pd.DataFrame({
                "code": df["code"],
                "end_date": df["end_date"],
                "pe": None,
                "pb": None,
                "roe": df["roe"] if "roe" in df.columns else None,
                "revenue": None,
                "net_profit": None,
                "revenue_yoy": df["or_yoy"] if "or_yoy" in df.columns else None,
                "profit_yoy": df["netprofit_yoy"] if "netprofit_yoy" in df.columns else None,
            })
            ProviderManager._upsert_finance(conn, out)
            wm.set_watermark(wm.DATASET_FINANCE, code, wm.STATUS_OK, len(out))
            synced += 1
            rows_total += len(out)
            if synced % 20 == 0 or synced == max_n:
                _log_progress(
                    "finance", "进度 stocks=%d/%d rows=%d last=%s",
                    synced, max_n, rows_total, code,
                )
        except Exception as exc:
            wm.set_watermark(wm.DATASET_FINANCE, code, wm.STATUS_ERROR, error=str(exc))
            logger.warning("财务同步 %s 失败: %s", code, exc)
            # 限流类错误向上抛，其它继续（统一用 rate_limit.classify_tushare_error）
            if rate_limit.classify_tushare_error(exc) is rate_limit.ErrorType.RATE_LIMIT:
                raise
    _log_done(
        "finance", "stock_finance", rows_total,
        stocks=synced, source="tushare.fina_indicator",
    )
    return synced


def fetch_daily_basic(code: str, trade_date: str = "") -> pd.DataFrame:
    """获取每日指标（PE/PB/换手率等）— 单票，经限速。"""
    kwargs = {"ts_code": code, "fields": "ts_code,trade_date,pe,pb,ps,total_mv,circ_mv,turnover_rate"}
    if trade_date:
        kwargs["trade_date"] = trade_date
    df = _pro_call("daily_basic", **kwargs)
    df = df.rename(columns={"ts_code": "code"})
    return df



# ─── 资金面：moneyflow / sector_moneyflow / market_moneyflow ─────────────────


def find_missing_moneyflow_dates(days: int = 252) -> list[date]:
    """期望最近 days 个交易日中，stock_moneyflow watermark 尚未 ok 的日期。"""
    pro = None
    try:
        if os.environ.get("TUSHARE_TOKEN"):
            pro = _get_pro()
    except Exception:
        pro = None
    expected = _resolve_trade_days(date.today(), days, pro or type("P", (), {})())
    return wm.find_daily_gaps(expected, dataset=wm.DATASET_MONEYFLOW)


def sync_moneyflow_to_db(
    trade_date: str | None = None,
    days: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
) -> int:
    """按交易日批量同步个股资金流向到 stock_moneyflow（金额单位：万元）。

    日期范围二选一：``start_date``/``end_date`` 按区间补全，或 ``days``
    取最近 N 个交易日。``overwrite=True`` 时强制重拉覆盖已有数据。
    """
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    range_mode = bool(start_date or end_date)
    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()

    if range_mode:
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else cursor
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else cursor
        if start_d > end_d:
            raise ValueError(f"start_date({start_d}) 不能晚于 end_date({end_d})")
        trade_days = _resolve_trade_days_range(start_d, end_d, pro)
        days = len(trade_days)
    else:
        if days <= 0:
            return 0
        trade_days = _resolve_trade_days(cursor, days, pro)
    if not trade_days:
        _log_skip("moneyflow", "无交易日")
        return 0

    fetched = 0
    skipped = 0
    _log_start(
        "moneyflow",
        table="stock_moneyflow",
        days=days,
        dates=len(trade_days),
        overwrite=overwrite,
        range_mode=range_mode,
    )

    for idx, d in enumerate(trade_days):
        iso = d.isoformat()
        if not overwrite and idx > 0 and iso in wm.ok_keys(wm.DATASET_MONEYFLOW, since=d):
            skipped += 1
            _log_progress("moneyflow", "%s 跳过（水位已 ok）", iso)
            continue
        try:
            df = _pro_call(
                "moneyflow",
                trade_date=d.strftime("%Y%m%d"),
                fields=(
                    "ts_code,trade_date,buy_sm_amount,sell_sm_amount,"
                    "buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,"
                    "buy_elg_amount,sell_elg_amount,net_mf_amount"
                ),
            )
            if df is None or df.empty:
                wm.set_watermark(wm.DATASET_MONEYFLOW, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("moneyflow", "%s 无数据", iso)
                continue
            df = df.rename(columns={"ts_code": "code"})
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
            cols = [
                "code", "trade_date", "buy_sm_amount", "sell_sm_amount",
                "buy_md_amount", "sell_md_amount", "buy_lg_amount", "sell_lg_amount",
                "buy_elg_amount", "sell_elg_amount", "net_mf_amount",
            ]
            df_insert = df[[c for c in cols if c in df.columns]]
            conn.register("_mf_df", df_insert)
            conn.execute(
                """
                INSERT INTO stock_moneyflow
                    (code, trade_date, buy_sm_amount, sell_sm_amount,
                     buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount,
                     buy_elg_amount, sell_elg_amount, net_mf_amount)
                SELECT code, trade_date, buy_sm_amount, sell_sm_amount,
                       buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount,
                       buy_elg_amount, sell_elg_amount, net_mf_amount FROM _mf_df
                ON CONFLICT (code, trade_date) DO UPDATE SET
                    buy_sm_amount=EXCLUDED.buy_sm_amount, sell_sm_amount=EXCLUDED.sell_sm_amount,
                    buy_md_amount=EXCLUDED.buy_md_amount, sell_md_amount=EXCLUDED.sell_md_amount,
                    buy_lg_amount=EXCLUDED.buy_lg_amount, sell_lg_amount=EXCLUDED.sell_lg_amount,
                    buy_elg_amount=EXCLUDED.buy_elg_amount, sell_elg_amount=EXCLUDED.sell_elg_amount,
                    net_mf_amount=EXCLUDED.net_mf_amount
                """
            )
            conn.unregister("_mf_df")
            fetched += len(df_insert)
            wm.set_watermark(wm.DATASET_MONEYFLOW, d, wm.STATUS_OK, len(df_insert))
            _log_progress("moneyflow", "%s 入库 %d 条 → stock_moneyflow", iso, len(df_insert))
        except Exception as exc:
            wm.set_watermark(wm.DATASET_MONEYFLOW, d, wm.STATUS_ERROR, error=str(exc))
            raise
    _log_done("moneyflow", "stock_moneyflow", fetched, skipped=skipped, source="tushare.moneyflow")
    return fetched


def sync_sector_moneyflow_to_db(trade_date: str | None = None, days: int = 1) -> int:
    """按交易日同步板块资金流向到 sector_moneyflow（行业/概念/地域三种类型）。"""
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()
    trade_days = _resolve_trade_days(cursor, days, pro)
    fetched = 0
    skipped = 0
    _log_start("sector_moneyflow", table="sector_moneyflow", days=days, dates=len(trade_days))

    _CONTENT_TYPES = ("行业", "概念", "地域")

    for idx, d in enumerate(trade_days):
        iso = d.isoformat()
        if idx > 0 and iso in wm.ok_keys(wm.DATASET_SECTOR_MONEYFLOW, since=d):
            skipped += 1
            _log_progress("sector_moneyflow", "%s 跳过（水位已 ok）", iso)
            continue
        day_fetched = 0
        try:
            for ct in _CONTENT_TYPES:
                df = _pro_call(
                    "moneyflow_ind_dc",
                    trade_date=d.strftime("%Y%m%d"),
                    content_type=ct,
                    fields=(
                        "trade_date,content_type,ts_code,name,pct_change,"
                        "net_amount,buy_elg_amount,buy_lg_amount,"
                        "buy_md_amount,buy_sm_amount,buy_sm_amount_stock"
                    ),
                )
                if df is None or df.empty:
                    continue
                df = df.rename(columns={"ts_code": "code", "buy_sm_amount_stock": "buy_sm_stock"})
                df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
                cols = [
                    "trade_date", "content_type", "code", "name", "pct_change",
                    "net_amount", "buy_elg_amount", "buy_lg_amount",
                    "buy_md_amount", "buy_sm_amount", "buy_sm_stock",
                ]
                df_insert = df[[c for c in cols if c in df.columns]]
                conn.register("_smf_df", df_insert)
                conn.execute(
                    """
                    INSERT INTO sector_moneyflow
                        (trade_date, content_type, code, name, pct_change,
                         net_amount, buy_elg_amount, buy_lg_amount,
                         buy_md_amount, buy_sm_amount, buy_sm_stock)
                    SELECT trade_date, content_type, code, name, pct_change,
                           net_amount, buy_elg_amount, buy_lg_amount,
                           buy_md_amount, buy_sm_amount, buy_sm_stock FROM _smf_df
                    ON CONFLICT (trade_date, content_type, code) DO UPDATE SET
                        name=EXCLUDED.name, pct_change=EXCLUDED.pct_change,
                        net_amount=EXCLUDED.net_amount, buy_elg_amount=EXCLUDED.buy_elg_amount,
                        buy_lg_amount=EXCLUDED.buy_lg_amount, buy_md_amount=EXCLUDED.buy_md_amount,
                        buy_sm_amount=EXCLUDED.buy_sm_amount, buy_sm_stock=EXCLUDED.buy_sm_stock
                    """
                )
                conn.unregister("_smf_df")
                day_fetched += len(df_insert)
            fetched += day_fetched
            wm.set_watermark(wm.DATASET_SECTOR_MONEYFLOW, d, wm.STATUS_OK, day_fetched)
            _log_progress("sector_moneyflow", "%s 入库 %d 条 → sector_moneyflow", iso, day_fetched)
        except Exception as exc:
            wm.set_watermark(wm.DATASET_SECTOR_MONEYFLOW, d, wm.STATUS_ERROR, error=str(exc))
            raise
    _log_done("sector_moneyflow", "sector_moneyflow", fetched, skipped=skipped, source="tushare.moneyflow_ind_dc")
    return fetched


def sync_market_moneyflow_to_db(trade_date: str | None = None, days: int = 1) -> int:
    """按交易日同步大盘资金流向到 market_moneyflow。"""
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()
    trade_days = _resolve_trade_days(cursor, days, pro)
    fetched = 0
    skipped = 0
    _log_start("market_moneyflow", table="market_moneyflow", days=days, dates=len(trade_days))

    for idx, d in enumerate(trade_days):
        iso = d.isoformat()
        if idx > 0 and iso in wm.ok_keys(wm.DATASET_MARKET_MONEYFLOW, since=d):
            skipped += 1
            _log_progress("market_moneyflow", "%s 跳过（水位已 ok）", iso)
            continue
        try:
            df = _pro_call(
                "moneyflow_mkt_dc",
                trade_date=d.strftime("%Y%m%d"),
                fields=(
                    "trade_date,pct_change_sh,pct_change_sz,net_amount,"
                    "buy_elg_amount,buy_lg_amount,buy_md_amount,buy_sm_amount"
                ),
            )
            if df is None or df.empty:
                wm.set_watermark(wm.DATASET_MARKET_MONEYFLOW, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("market_moneyflow", "%s 无数据", iso)
                continue
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
            cols = [
                "trade_date", "pct_change_sh", "pct_change_sz", "net_amount",
                "buy_elg_amount", "buy_lg_amount", "buy_md_amount", "buy_sm_amount",
            ]
            df_insert = df[[c for c in cols if c in df.columns]]
            conn.register("_mmf_df", df_insert)
            conn.execute(
                """
                INSERT INTO market_moneyflow
                    (trade_date, pct_change_sh, pct_change_sz, net_amount,
                     buy_elg_amount, buy_lg_amount, buy_md_amount, buy_sm_amount)
                SELECT trade_date, pct_change_sh, pct_change_sz, net_amount,
                       buy_elg_amount, buy_lg_amount, buy_md_amount, buy_sm_amount FROM _mmf_df
                ON CONFLICT (trade_date) DO UPDATE SET
                    pct_change_sh=EXCLUDED.pct_change_sh, pct_change_sz=EXCLUDED.pct_change_sz,
                    net_amount=EXCLUDED.net_amount, buy_elg_amount=EXCLUDED.buy_elg_amount,
                    buy_lg_amount=EXCLUDED.buy_lg_amount, buy_md_amount=EXCLUDED.buy_md_amount,
                    buy_sm_amount=EXCLUDED.buy_sm_amount
                """
            )
            conn.unregister("_mmf_df")
            fetched += len(df_insert)
            wm.set_watermark(wm.DATASET_MARKET_MONEYFLOW, d, wm.STATUS_OK, len(df_insert))
            _log_progress("market_moneyflow", "%s 入库 %d 条 → market_moneyflow", iso, len(df_insert))
        except Exception as exc:
            wm.set_watermark(wm.DATASET_MARKET_MONEYFLOW, d, wm.STATUS_ERROR, error=str(exc))
            raise
    _log_done("market_moneyflow", "market_moneyflow", fetched, skipped=skipped, source="tushare.moneyflow_mkt_dc")
    return fetched


# ─── 逻辑面：concept_board / concept_member ─────────────────────────────────


def sync_concept_board_to_db(trade_date: str | None = None, days: int = 1) -> int:
    """按交易日同步概念题材板块到 concept_board（Tushare dc_concept）。

    dc_concept 数据从 2026-02-03 开始；backfill 不会早于此日期。
    """
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()
    trade_days = _resolve_trade_days(cursor, days, pro)
    # ponytail: dc_concept 数据起始 2026-02-03，过滤掉更早的日期
    _min_date = date(2026, 2, 3)
    trade_days = [d for d in trade_days if d >= _min_date]

    fetched = 0
    skipped = 0
    _log_start("concept_board", table="concept_board", days=days, dates=len(trade_days))

    for idx, d in enumerate(trade_days):
        iso = d.isoformat()
        if idx > 0 and iso in wm.ok_keys(wm.DATASET_CONCEPT_BOARD, since=d):
            skipped += 1
            _log_progress("concept_board", "%s 跳过（水位已 ok）", iso)
            continue
        try:
            df = _pro_call(
                "dc_concept",
                trade_date=d.strftime("%Y%m%d"),
                fields=(
                    "theme_code,trade_date,name,pct_change,hot,sort,strength,"
                    "z_t_num,main_change,lead_stock,lead_stock_code,lead_stock_pct_change"
                ),
            )
            if df is None or df.empty:
                wm.set_watermark(wm.DATASET_CONCEPT_BOARD, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("concept_board", "%s 无数据", iso)
                continue
            df = df.rename(columns={
                "theme_code": "code",
                "z_t_num": "zt_num",
                "lead_stock_pct_change": "lead_stock_pct",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date
            cols = [
                "trade_date", "code", "name", "pct_change", "hot", "sort",
                "strength", "zt_num", "main_change",
                "lead_stock", "lead_stock_code", "lead_stock_pct",
            ]
            df_insert = df[[c for c in cols if c in df.columns]]
            conn.register("_cb_df", df_insert)
            conn.execute(
                """
                INSERT INTO concept_board
                    (trade_date, code, name, pct_change, hot, sort,
                     strength, zt_num, main_change,
                     lead_stock, lead_stock_code, lead_stock_pct)
                SELECT trade_date, code, name, pct_change, hot, sort,
                       strength, zt_num, main_change,
                       lead_stock, lead_stock_code, lead_stock_pct FROM _cb_df
                ON CONFLICT (trade_date, code) DO UPDATE SET
                    name=EXCLUDED.name, pct_change=EXCLUDED.pct_change, hot=EXCLUDED.hot,
                    sort=EXCLUDED.sort, strength=EXCLUDED.strength, zt_num=EXCLUDED.zt_num,
                    main_change=EXCLUDED.main_change, lead_stock=EXCLUDED.lead_stock,
                    lead_stock_code=EXCLUDED.lead_stock_code, lead_stock_pct=EXCLUDED.lead_stock_pct
                """
            )
            conn.unregister("_cb_df")
            fetched += len(df_insert)
            wm.set_watermark(wm.DATASET_CONCEPT_BOARD, d, wm.STATUS_OK, len(df_insert))
            _log_progress("concept_board", "%s 入库 %d 条 → concept_board", iso, len(df_insert))
        except Exception as exc:
            wm.set_watermark(wm.DATASET_CONCEPT_BOARD, d, wm.STATUS_ERROR, error=str(exc))
            raise
    _log_done("concept_board", "concept_board", fetched, skipped=skipped, source="tushare.dc_concept")
    return fetched


def sync_concept_member_to_db(
    trade_date: str | None = None,
    days: int = 1,
    top_n: int | None = 24,
) -> int:
    """按交易日同步概念题材成分股到 concept_member。

    默认仅同步主线相关 TOP 概念（top_n，约 sector_top_n*3），避免全市场数百
    theme_code 循环导致同步失败。传 top_n=None 可恢复全量同步。
    """
    from xshare.data.db import get_conn, init_tables

    pro = _get_pro()
    conn = get_conn()
    init_tables(conn)

    cursor = datetime.strptime(trade_date, "%Y%m%d").date() if trade_date else date.today()
    trade_days = _resolve_trade_days(cursor, days, pro)
    _min_date = date(2026, 2, 3)
    trade_days = [d for d in trade_days if d >= _min_date]

    fetched = 0
    skipped = 0
    _log_start("concept_member", table="concept_member", days=days, dates=len(trade_days))

    for idx, d in enumerate(trade_days):
        iso = d.isoformat()
        if idx > 0 and iso in wm.ok_keys(wm.DATASET_CONCEPT_MEMBER, since=d):
            skipped += 1
            _log_progress("concept_member", "%s 跳过（水位已 ok）", iso)
            continue
        day_fetched = 0
        try:
            if top_n is not None:
                codes = [
                    r[0]
                    for r in conn.execute(
                        """
                        SELECT code FROM concept_board
                        WHERE trade_date = ?
                        ORDER BY COALESCE(hot, 0) DESC, COALESCE(zt_num, 0) DESC
                        LIMIT ?
                        """,
                        [d, top_n],
                    ).fetchall()
                ]
            else:
                codes = [
                    r[0]
                    for r in conn.execute(
                        "SELECT code FROM concept_board WHERE trade_date = ?", [d]
                    ).fetchall()
                ]
            if not codes:
                # concept_board 可能因并行调度竞态尚未写入；先同步 concept_board 再重读。
                _log_progress("concept_member", "%s concept_board 无数据，先同步 concept_board", iso)
                try:
                    sync_concept_board_to_db(trade_date=d.strftime("%Y%m%d"))
                    codes = [
                        r[0]
                        for r in conn.execute(
                            """
                            SELECT code FROM concept_board
                            WHERE trade_date = ?
                            ORDER BY COALESCE(hot, 0) DESC, COALESCE(zt_num, 0) DESC
                            LIMIT ?
                            """,
                            [d, top_n],
                        ).fetchall()
                    ] if top_n is not None else [
                        r[0]
                        for r in conn.execute(
                            "SELECT code FROM concept_board WHERE trade_date = ?", [d]
                        ).fetchall()
                    ]
                except Exception as cb_exc:
                    _log_progress("concept_member", "%s concept_board 补同步失败: %s", iso, cb_exc)
            if not codes:
                _log_progress("concept_member", "%s concept_board 无数据，跳过", iso)
                wm.set_watermark(wm.DATASET_CONCEPT_MEMBER, d, wm.STATUS_ERROR, 0, "no concept_board")
                continue

            frames = []
            for tc in codes:
                df = _pro_call(
                    "dc_concept_cons",
                    trade_date=d.strftime("%Y%m%d"),
                    theme_code=tc,
                    fields="ts_code,trade_date,name,theme_code,industry,reason,hot_num",
                )
                if df is None or df.empty:
                    continue
                frames.append(df)

            if not frames:
                wm.set_watermark(wm.DATASET_CONCEPT_MEMBER, d, wm.STATUS_ERROR, 0, "empty")
                _log_progress("concept_member", "%s 无数据", iso)
                continue

            df_all = pd.concat(frames, ignore_index=True)
            df_all = df_all.rename(columns={"ts_code": "code", "theme_code": "concept_code"})
            df_all["trade_date"] = pd.to_datetime(df_all["trade_date"], format="%Y%m%d").dt.date
            # hot_num 可能为字符串，转 int
            df_all["hot_num"] = pd.to_numeric(df_all["hot_num"], errors="coerce").astype("Int64")
            cols = ["trade_date", "code", "name", "concept_code", "industry", "reason", "hot_num"]
            df_insert = df_all[[c for c in cols if c in df_all.columns]]
            conn.register("_cm_df", df_insert)
            conn.execute(
                """
                INSERT INTO concept_member
                    (trade_date, code, name, concept_code, industry, reason, hot_num)
                SELECT trade_date, code, name, concept_code, industry, reason, hot_num FROM _cm_df
                ON CONFLICT (trade_date, code, concept_code) DO UPDATE SET
                    name=EXCLUDED.name, industry=EXCLUDED.industry,
                    reason=EXCLUDED.reason, hot_num=EXCLUDED.hot_num
                """
            )
            conn.unregister("_cm_df")
            day_fetched = len(df_insert)
            fetched += day_fetched
            wm.set_watermark(wm.DATASET_CONCEPT_MEMBER, d, wm.STATUS_OK, day_fetched)
            _log_progress("concept_member", "%s 入库 %d 条 → concept_member", iso, day_fetched)
        except Exception as exc:
            wm.set_watermark(wm.DATASET_CONCEPT_MEMBER, d, wm.STATUS_ERROR, error=str(exc))
            raise
    _log_done("concept_member", "concept_member", fetched, skipped=skipped, source="tushare.dc_concept_cons")
    return fetched