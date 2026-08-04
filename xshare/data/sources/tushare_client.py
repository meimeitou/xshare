"""统一的 Tushare API 客户端：全局限速 + 重试退避 + 错误分类。

同步路径（``tushare_source._pro_call``）与读路径（``TushareProvider``）
共用此客户端，消除两套限速/重试逻辑的重复实现。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

import pandas as pd

from xshare.data import rate_limit
from xshare.utils import env_int, env_float

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """读取环境变量为非负 int（<0 回退 default）。"""
    v = env_int(name, default)
    return v if v >= 0 else default


def _env_float(name: str, default: float) -> float:
    """读取环境变量为正 float（<=0 回退 default）。"""
    v = env_float(name, default)
    return v if v > 0 else default


class TushareClient:
    """带全局限速与重试退避的 Tushare API 客户端。

    包装一个 ``caller`` 可调用对象 ``(method: str, kwargs: dict) -> pd.DataFrame``
    ——由调用方决定实际传输方式（tushare SDK 或 HTTPS 直连），客户端本身
    只负责限速与重试，不感知传输细节。

    重试预算：
    - 频率超限（``rate_limit_error``）：冷却 ``rate_cooldown`` 秒后重试，最多
      ``rate_retries`` 次。每次冷却同时调用 ``rate_limit.penalty`` 推迟全局限速器，
      使其他调用方（读路径 / 同步 worker）同步等待。
    - 瞬时网络错误（``transient_error``）：指数退避 2/4/8s，最多 ``net_retries`` 次。
    - 两种预算互相独立：频率超限重试不消耗网络重试配额，反之亦然。
    """

    def __init__(
        self,
        caller: Callable[[str, dict], pd.DataFrame],
        *,
        rate_retries: int | None = None,
        rate_cooldown: float | None = None,
        net_retries: int | None = None,
    ):
        self._caller = caller
        self._rate_retries = (
            rate_retries
            if rate_retries is not None
            else _env_int("XSHARE_TUSHARE_RATE_RETRIES", 5)
        )
        self._rate_cooldown = (
            rate_cooldown
            if rate_cooldown is not None
            else _env_float("XSHARE_TUSHARE_RATE_COOLDOWN", 65.0)
        )
        self._net_retries = (
            net_retries
            if net_retries is not None
            else _env_int("XSHARE_TUSHARE_NET_RETRIES", 3)
        )

    def call(self, method: str, **kwargs) -> pd.DataFrame:
        """调用 Tushare API 方法，带全局限速与重试退避。

        ``kwargs`` 原样传给 ``caller``；``caller`` 自行决定是否提取 ``fields``
        （HTTPS 直连模式）或将其透传给 SDK。
        """
        last_exc: Exception | None = None
        net_attempt = 0
        total_attempts = max(self._rate_retries, 0) + max(self._net_retries, 0) + 1
        for attempt in range(total_attempts):
            rate_limit.acquire("tushare")
            try:
                return self._caller(method, kwargs)
            except Exception as exc:
                last_exc = exc
                err_type = rate_limit.classify_tushare_error(exc)
                if err_type is rate_limit.ErrorType.RATE_LIMIT and attempt < self._rate_retries:
                    logger.warning(
                        "Tushare.%s 频率超限，冷却 %.0fs 后重试 (%d/%d): %s",
                        method,
                        self._rate_cooldown,
                        attempt + 1,
                        self._rate_retries,
                        exc,
                    )
                    rate_limit.penalty("tushare", self._rate_cooldown)
                    time.sleep(self._rate_cooldown)
                    continue
                if err_type is rate_limit.ErrorType.TRANSIENT and net_attempt < self._net_retries:
                    backoff = 2.0 * (2 ** net_attempt)
                    net_attempt += 1
                    logger.warning(
                        "Tushare.%s 瞬时网络错误，%.1fs 后重试 (%d/%d): %s",
                        method,
                        backoff,
                        net_attempt,
                        self._net_retries,
                        exc,
                    )
                    time.sleep(backoff)
                    continue
                raise
        assert last_exc is not None
        raise last_exc
