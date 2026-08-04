"""统一日志配置，确保 Web / MCP 都能看到 xshare 同步日志。"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure_logging(force: bool = False) -> None:
    """配置 root + xshare logger，输出到 stderr（与 uvicorn 并存）。

    Web 经 uvicorn 启动时默认不 basicConfig，导致 ``xshare.*`` 的 INFO 可能不出现；
    在 lifespan / CLI 入口显式配置一次。
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    level_name = os.environ.get("XSHARE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(stream=sys.stderr, level=level, format=fmt, datefmt=datefmt)
    else:
        root.setLevel(level)

    # 保证业务包日志可见（不被第三方库抬高阈值）
    for name in ("xshare", "xshare.data", "xshare.data.sources", "xshare.data.task_queue"):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True

    # 屏蔽 akshare 内部 tqdm 进度条输出（全市场快照分页拉取会打印 0/58 之类
    # 的进度条污染日志）。tqdm 读取 TQDM_DISABLE 环境变量；需在 akshare 首次
    # 调用前设置，此处 configure_logging 在启动入口早于任何 akshare 调用。
    if os.environ.get("XSHARE_AKSHARE_DISABLE_TQDM", "1") not in ("0", "false", "False"):
        os.environ.setdefault("TQDM_DISABLE", "1")

    _CONFIGURED = True
