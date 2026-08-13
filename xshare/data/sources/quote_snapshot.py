"""行情快照同步：akshare 新浪源 → DuckDB 快照表。

由 quote 同步任务调用（交易时段每 5 分钟）。直接调用 akshare_provider
的无缓存抓取函数，不经 ProviderManager（其读路径缓存优先，会形成循环）。
个股/指数/板块三路抓取相互独立，单路失败不影响其它路入库。
"""

import logging
from datetime import datetime

from xshare.data.quote_cache import purge, write_snapshots
from xshare.utils import env_int

logger = logging.getLogger(__name__)


def sync_quote_snapshot_to_db() -> int:
    from xshare.data.sources.akshare_provider import (
        _fetch_index_spot_sina,
        _fetch_sector_spot_sina,
        _fetch_spot_sina,
    )

    ts = datetime.now().replace(microsecond=0)
    frames = []
    for name, fetch in (
        ("spot", _fetch_spot_sina),
        ("index", _fetch_index_spot_sina),
        ("sector", _fetch_sector_spot_sina),
    ):
        try:
            frames.append(fetch())
        except Exception as exc:
            logger.warning("行情快照 %s 抓取失败: %s", name, exc)
            frames.append(None)
    if all(f is None or f.empty for f in frames):
        raise RuntimeError("行情快照三路抓取全部失败")

    written = write_snapshots(frames[0], frames[1], frames[2], ts)
    retain = env_int("XSHARE_QUOTE_RETAIN_DAYS", 5)
    if retain > 0:
        purged = purge(retain)
        if purged:
            logger.info("行情快照清理: 删除 %d 条（保留 %d 天）", purged, retain)
    return written
