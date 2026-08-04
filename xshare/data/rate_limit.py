"""按数据源的全局限速器（最小间隔 / 简易令牌桶）。

sync worker 与 Provider ``force_refresh`` 共用，避免读路径与后台同步
叠加打爆 Tushare / AkShare 配额。

也提供 Tushare 错误分类（``ErrorType`` / ``classify_tushare_error``），
供 ``TushareClient`` 决定重试策略。
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ErrorType(enum.Enum):
    """Tushare API 错误分类。"""

    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class TushareRateLimitError(Exception):
    """Tushare 频率超限错误。

    由 ``_http_call`` 在 API 返回频率超限消息时显式抛出，
    使 ``TushareClient`` 可通过 ``isinstance`` 判定而非字符串匹配。
    """


_RATE_LIMIT_PATTERNS = (
    "频率超限", "访问频率", "每分钟", "max_limit", "freq limit", "rate limit",
)

_TRANSIENT_PATTERNS = (
    "Connection reset",
    "Connection aborted",
    "Connection refused",
    "RemoteDisconnected",
    "ReadTimeout",
    "ReadTimeoutError",
    "ConnectTimeout",
    "ConnectTimeoutError",
    "Max retries exceeded",
    "chunked encoding",
    "IncompleteRead",
)

_TRANSIENT_EXC_TYPES = (ConnectionResetError, ConnectionAbortedError, ConnectionError, TimeoutError)


from xshare.utils import env_float as _env_float  # noqa: E402


@dataclass
class RateLimiter:
    """线程安全的最小间隔限速器。"""

    name: str
    min_interval: float
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_at: float = field(default=0.0, repr=False)
    wait_count: int = field(default=0, repr=False)
    call_count: int = field(default=0, repr=False)

    def acquire(self) -> float:
        """阻塞直到可调用，返回实际等待秒数。"""
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_at)
            if wait > 0:
                self.wait_count += 1
            else:
                wait = 0.0
            # 预占时间戳，避免并发穿透
            self._last_at = now + max(wait, 0.0)
            self.call_count += 1
        if wait > 0:
            time.sleep(wait)
            logger.debug("rate_limit[%s] waited %.3fs", self.name, wait)
        return wait

    def penalty(self, seconds: float) -> None:
        """遇到上游限流时推迟下一次可调用时间。"""
        if seconds <= 0:
            return
        with self._lock:
            self._last_at = max(self._last_at, time.monotonic() + seconds)
            logger.info("rate_limit[%s] penalty +%.0fs", self.name, seconds)

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "min_interval": self.min_interval,
                "call_count": self.call_count,
                "wait_count": self.wait_count,
            }


_limiters: dict[str, RateLimiter] = {}
_registry_lock = threading.Lock()

# 默认：tushare ~4 QPS（240/min，低于官方 500/min 留余量）；akshare ~2/s
_DEFAULTS = {
    "tushare": ("XSHARE_TUSHARE_MIN_INTERVAL", 0.25),
    "akshare": ("XSHARE_AKSHARE_MIN_INTERVAL", 0.5),
}


def get_limiter(source: str) -> RateLimiter:
    """获取（或创建）指定数据源的限速器。"""
    key = source.lower().strip()
    with _registry_lock:
        if key not in _limiters:
            env_name, default = _DEFAULTS.get(key, (f"XSHARE_{key.upper()}_MIN_INTERVAL", 0.5))
            # 兼容日线专用 QPS 环境变量
            if key == "tushare":
                qps = os.environ.get("XSHARE_DAILY_SYNC_QPS")
                if qps:
                    try:
                        q = float(qps)
                        if q > 0:
                            default = 1.0 / q
                    except (TypeError, ValueError):
                        pass
            interval = _env_float(env_name, default)
            _limiters[key] = RateLimiter(name=key, min_interval=interval)
        return _limiters[key]


def acquire(source: str) -> float:
    """在调用外部 API 前获取令牌。"""
    return get_limiter(source).acquire()


def penalty(source: str, seconds: float) -> None:
    """上游限流后推迟该源下一次调用。"""
    get_limiter(source).penalty(seconds)


def classify_tushare_error(exc: BaseException) -> ErrorType:
    """将 Tushare API 异常分类为 ``ErrorType``。

    优先用异常类型（``isinstance``）判定，兜底字符串匹配——
    Tushare SDK / HTTP 端点返回的错误消息格式不统一，字符串匹配作为
    最后防线确保不遗漏。
    """
    if isinstance(exc, TushareRateLimitError):
        return ErrorType.RATE_LIMIT
    if isinstance(exc, _TRANSIENT_EXC_TYPES):
        return ErrorType.TRANSIENT
    msg = str(exc)
    if any(key in msg for key in _RATE_LIMIT_PATTERNS):
        return ErrorType.RATE_LIMIT
    if any(key in msg for key in _TRANSIENT_PATTERNS):
        return ErrorType.TRANSIENT
    return ErrorType.PERMANENT


def is_rate_limit_error(exc: BaseException) -> bool:
    """识别 Tushare / 同类频率超限错误（向后兼容，委托 classify_tushare_error）。"""
    return classify_tushare_error(exc) is ErrorType.RATE_LIMIT


def is_transient_error(exc: BaseException) -> bool:
    """识别瞬时网络错误（连接被重置 / 中断 / 超时）。

    Tushare 走 HTTP，服务端偶发 ``Connection reset by peer`` /
    ``Connection aborted`` / ``ReadTimeout`` 时直接重试通常即可恢复，
    不应让整个同步任务因此失败。
    """
    return classify_tushare_error(exc) is ErrorType.TRANSIENT


def all_stats() -> list[dict]:
    with _registry_lock:
        return [lim.stats() for lim in _limiters.values()]


def reset_for_tests() -> None:
    """测试用：清空限速器状态。"""
    with _registry_lock:
        _limiters.clear()
