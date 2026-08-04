"""股票代码模糊匹配"""

import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass

import pandas as pd

from xshare.data.db import get_conn

try:
    from pypinyin import Style, lazy_pinyin
except Exception:  # pragma: no cover
    Style = None
    lazy_pinyin = None


_NAME_PINYIN_CACHE: dict[str, tuple[str, str]] = {}
_NAME_PINYIN_LOCK = threading.Lock()
_NAME_PINYIN_CACHE_MAX = 5000  # 限制内存增长，避免长跑服务无界膨胀


def _cache_pinyin(name: str, result: tuple[str, str]) -> tuple[str, str]:
    """线程安全地写入拼音缓存，达到上限时清空重来。"""
    with _NAME_PINYIN_LOCK:
        if len(_NAME_PINYIN_CACHE) >= _NAME_PINYIN_CACHE_MAX:
            _NAME_PINYIN_CACHE.clear()
        _NAME_PINYIN_CACHE[name] = result
    return result


@dataclass
class _ScoredMatch:
    code: str
    name: str
    score: int


def _normalize_ascii(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _get_name_pinyin(name: str) -> tuple[str, str]:
    with _NAME_PINYIN_LOCK:
        cached = _NAME_PINYIN_CACHE.get(name)
    if cached:
        return cached
    if lazy_pinyin is None:
        return _cache_pinyin(name, ("", ""))

    full = "".join(lazy_pinyin(name, errors="ignore")).lower()
    initials = ""
    if Style is not None:
        initials = "".join(
            lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore")
        ).lower()
    result = (_normalize_ascii(full), _normalize_ascii(initials))
    return _cache_pinyin(name, result)


def _score_row(code: str, name: str, query: str) -> int:
    q_raw = query.lower()
    q_ascii = _normalize_ascii(query)
    code_raw = code.lower()
    code_ascii = _normalize_ascii(code)
    name_raw = name.lower()

    score = 0

    if q_ascii:
        if code_ascii == q_ascii:
            score = max(score, 130)
        elif code_ascii.startswith(q_ascii):
            score = max(score, 120)
        elif q_ascii in code_ascii:
            score = max(score, 75)

    if q_raw:
        if name_raw == q_raw:
            score = max(score, 115)
        elif name_raw.startswith(q_raw):
            score = max(score, 105)
        elif q_raw in name_raw:
            score = max(score, 80)

    if q_ascii:
        full_pinyin, initials = _get_name_pinyin(name)
        if full_pinyin:
            if full_pinyin.startswith(q_ascii):
                score = max(score, 95)
            elif q_ascii in full_pinyin:
                score = max(score, 70)
        if initials:
            if initials.startswith(q_ascii):
                score = max(score, 90)
            elif q_ascii in initials:
                score = max(score, 65)

    return score


def _search_local(conn, query: str, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT code, name FROM stock_basic WHERE code IS NOT NULL AND name IS NOT NULL"
    ).fetchall()

    scored: list[_ScoredMatch] = []
    for code, name in rows:
        score = _score_row(str(code), str(name), query)
        if score > 0:
            scored.append(_ScoredMatch(code=str(code), name=str(name), score=score))

    scored.sort(key=lambda x: (-x.score, x.code, x.name))
    return [{"code": s.code, "name": s.name} for s in scored[:limit]]


async def stock_resolve(args: dict) -> str:
    """模糊匹配股票代码，支持名称、代码、全拼、首字母。"""
    query = args["query"].strip()
    conn = get_conn()
    if not query:
        return json.dumps({"matches": []}, ensure_ascii=False)

    # 1. 优先查询本地数据库
    matches = _search_local(conn, query=query, limit=10)
    if matches:
        return json.dumps({"matches": matches}, ensure_ascii=False)

    # 2. 本地无结果且 Tushare 可用时，回落到 API 查询并写入本地
    if os.environ.get("TUSHARE_TOKEN"):
        try:
            from xshare.data.sources.tushare_source import _get_pro, upsert_stocks_to_db

            pro = _get_pro()
            fields = "ts_code,name,market,industry,list_date"
            dfs: list[pd.DataFrame] = []

            # 按代码查询（支持 6 位纯数字或带交易所后缀）
            if re.match(r"^\d{6}(\.(SH|SZ|BJ))?$", query.upper()):
                df_code = await asyncio.to_thread(
                    lambda: pro.stock_basic(ts_code=query.upper(), fields=fields)
                )
                if not df_code.empty:
                    dfs.append(df_code)

            # 按名称精确查询
            df_name = await asyncio.to_thread(
                lambda: pro.stock_basic(name=query, fields=fields)
            )
            if not df_name.empty:
                dfs.append(df_name)

            if dfs:
                result = pd.concat(dfs).drop_duplicates("ts_code").head(10)
                result = result.rename(columns={"ts_code": "code"})
                result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce").dt.date
                upsert_stocks_to_db(result)
                matches = [{"code": r["code"], "name": r["name"]} for _, r in result.iterrows()]
                return json.dumps({"matches": matches}, ensure_ascii=False)
        except Exception:
            pass

    return json.dumps({"matches": [], "message": f"未找到匹配: {query}"}, ensure_ascii=False)
