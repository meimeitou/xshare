"""共享工具：超时调用、JSON 序列化、环境变量读取、常量。"""

from __future__ import annotations

import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ─── 超时调用 ──────────────────────────────────────────────────────────────────

# safe_call 每次创建 max_workers=1 的临时池，无需全局池。


def safe_call(fn: Callable, *args, timeout: float = 15, **kwargs) -> Any:
    """带超时的同步调用包装。

    超时后用非阻塞 shutdown 退出，避免被卡死的底层线程在 ``with`` 退出时无限等待
    （akshare 网络挂起的根因）。调用方应捕获 ``TimeoutError``。
    """
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=timeout)
    except TimeoutError:
        logger.warning("safe_call 超时（%ss）：已放弃该调用", timeout)
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ─── JSON 序列化辅助 ──────────────────────────────────────────────────────────


def to_json_safe(obj: Any) -> Any:
    """递归将 numpy/pandas/NaN/date 等转为 JSON 可序列化的 Python 内建类型。

    统一实现，替代散落各处的 ``json_safe``/``_to_json_safe``/``_to_builtin``。
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_safe(v) for v in obj]
    # numpy / pandas 标量
    if hasattr(obj, "item"):
        try:
            return to_json_safe(obj.item())
        except Exception:
            pass
    # 日期/时间
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            pass
    # float NaN/Inf → None（非法 JSON 值）
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


# ─── 环境变量辅助 ──────────────────────────────────────────────────────────────


def env_int(key: str, default: int) -> int:
    """读取环境变量为 int，空字符串/异常回退 default。"""
    raw = os.environ.get(key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def env_float(key: str, default: float) -> float:
    """读取环境变量为 float，空字符串/异常回退 default。"""
    raw = os.environ.get(key, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def env_str(key: str, default: str) -> str:
    """读取环境变量为 str，空字符串回退 default。"""
    raw = os.environ.get(key, "")
    return raw if raw else default


# ─── 共享常量 ──────────────────────────────────────────────────────────────────

TRADING_DAYS_PER_YEAR = 252

# 涨跌停阈值（普通主板约 ±10%，ST/创业板/科创板更宽，此处仅用于粗筛）
LIMIT_UP_THRESHOLD = 9.9
LIMIT_DOWN_THRESHOLD = -9.9

# 数据覆盖率达标阈值
COVERAGE_SUFFICIENCY = 0.95

# 新闻内容截断长度
NEWS_CONTENT_TRUNCATE = 500

# 缓存 TTL
STALE_CACHE_DAYS = 7
BASIC_INFO_TTL_HOURS = 24
