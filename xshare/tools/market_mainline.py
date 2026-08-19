"""主线行情识别"""

import asyncio
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from xshare.data.db import get_conn
from xshare.data.provider import get_provider
from xshare.indicators.technical import calculate_indicators
from xshare.utils import safe_call, to_json_safe


# 用于并发拉取个股日线 + 指标计算。akshare/tushare 调用本身在线程池里跑，
# 这里只是把串行的 N 次循环并发提交。
_mainline_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mkt-mainline")


def _mark_northbound_stale(northbound: dict) -> None:
    """标记北向资金是否为非当日数据（盘中无实时来源，回退昨日）。

    2024-08-19 起东财停止披露北向资金盘中实时数据，Tushare moneyflow_hsgt
    为日终接口，盘中取不到当日值。当 northbound.date != 今天时加 is_stale/note，
    让前端与 AI 显式提示用户展示的是前一交易日数据。
    """
    if not isinstance(northbound, dict):
        return
    today = datetime.now().strftime("%Y%m%d")
    nb_date = str(northbound.get("date", "")).replace("-", "")
    northbound["is_stale"] = nb_date != today
    if northbound["is_stale"]:
        northbound["note"] = "盘中无北向资金实时数据，展示前一交易日日终数据"


def _calc_market_phase(indices: dict, market_stats: dict, northbound: dict) -> str:
    """根据指数、涨跌家数、北向资金判断市场阶段"""
    changes = []
    for v in indices.values():
        if isinstance(v, dict) and v.get("change_pct") is not None:
            changes.append(float(v["change_pct"]))
    avg_index_change = sum(changes) / len(changes) if changes else 0.0

    total = int(market_stats.get("total", 0) or 0)
    up = int(market_stats.get("up", 0) or 0)
    down = int(market_stats.get("down", 0) or 0)
    breadth = ((up - down) / total * 100) if total > 0 else 0.0

    north_total = float(northbound.get("total", 0) or 0)

    if avg_index_change >= 1.0 and breadth >= 10 and north_total > 0:
        return "风险偏好上行（主升）"
    if avg_index_change <= -1.0 and breadth <= -10 and north_total < 0:
        return "风险偏好下降（退潮）"
    if avg_index_change > 0 and breadth > 0:
        return "震荡偏强（结构性轮动）"
    if avg_index_change < 0 and breadth < 0:
        return "震荡偏弱（防守为主）"
    return "分化震荡（关注主线板块）"


def _normalize_code(code: str) -> str:
    """统一代码格式（去掉交易所后缀）"""
    return str(code).split(".")[0]


def _infer_mainline_sectors_from_movers(provider, sector_top_n: int) -> list[dict]:
    """板块接口不可用时，基于涨幅榜 + 股票行业字段推断主线板块"""
    gainers, _ = safe_call(provider.get_top_movers, 80)
    stock_list = safe_call(provider.get_stock_list)

    if stock_list is None or getattr(stock_list, "empty", True):
        return []
    if "code" not in stock_list.columns or "industry" not in stock_list.columns:
        return []

    industry_map = {}
    for _, row in stock_list.iterrows():
        industry = row.get("industry")
        if not industry:
            continue
        industry_map[_normalize_code(row.get("code"))] = str(industry)

    bucket = {}
    for mover in gainers:
        industry = industry_map.get(_normalize_code(mover.code))
        if not industry:
            continue
        data = bucket.setdefault(
            industry,
            {
                "name": industry,
                "sum_change": 0.0,
                "count": 0,
                "leader": mover.name,
                "leader_pct": float(mover.change_pct),
            },
        )
        change_pct = float(mover.change_pct)
        data["sum_change"] += change_pct
        data["count"] += 1
        if change_pct > data["leader_pct"]:
            data["leader"] = mover.name
            data["leader_pct"] = change_pct

    sectors = []
    for _, v in bucket.items():
        if v["count"] <= 0:
            continue
        avg_change = v["sum_change"] / v["count"]
        tag = "主线" if avg_change >= 3 else ("强势" if avg_change >= 2 else "观察")
        sectors.append(
            {
                "name": v["name"],
                "change_pct": round(avg_change, 2),
                "leader": v["leader"],
                "leader_pct": round(v["leader_pct"], 2),
                "strength_tag": tag,
            }
        )

    sectors.sort(key=lambda x: x["change_pct"], reverse=True)
    return sectors[:sector_top_n]


# ─── 离线三维度共振主线分析 ──────────────────────────────────────────────────



def _trading_day_lag(conn, earlier, later) -> int:
    """earlier 与 later 之间的交易日数（later 更新时 > 0）。"""
    if earlier is None or later is None or later <= earlier:
        return 0
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM trade_cal
            WHERE cal_date > ? AND cal_date <= ? AND is_open = 1
            """,
            [earlier, later],
        ).fetchone()
        if row is not None:
            return int(row[0])
    except Exception:
        pass
    if isinstance(earlier, str):
        earlier = date.fromisoformat(str(earlier)[:10])
    if isinstance(later, str):
        later = date.fromisoformat(str(later)[:10])
    return max(0, (later - earlier).days)


def _rank_desc(values: list[float]) -> list[int]:
    """降序排名（1=最大）。"""
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    ranks = [0] * len(values)
    for rank, idx in enumerate(order, start=1):
        ranks[idx] = rank
    return ranks
def _score_mainline_from_db(sector_top_n: int, strong_limit: int) -> dict | None:
    """离线三维度共振主线分析。数据不足时返回 None，调用方降级到实时路径。

    三维度：
    - 逻辑面：concept_board 热度/涨停数排序
    - 资金面：sector_moneyflow 主力净流入 + market_moneyflow 大盘资金
    - 情绪面：limit_list 涨停梯队（本地计算）
    """
    conn = get_conn()
    data_warnings: list[str] = []

    stock_daily_latest = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily"
    ).fetchone()[0]
    if stock_daily_latest is None:
        return None

    concept_latest = conn.execute(
        "SELECT MAX(trade_date) FROM concept_board"
    ).fetchone()[0]
    limit_latest = conn.execute(
        "SELECT MAX(trade_date) FROM limit_list"
    ).fetchone()[0]
    sector_mf_latest = conn.execute(
        "SELECT MAX(trade_date) FROM sector_moneyflow WHERE content_type = '概念'"
    ).fetchone()[0]
    stock_mf_latest = conn.execute(
        "SELECT MAX(trade_date) FROM stock_moneyflow"
    ).fetchone()[0]
    member_latest = conn.execute(
        "SELECT MAX(trade_date) FROM concept_member"
    ).fetchone()[0]

    if concept_latest is None or limit_latest is None:
        return None

    latest_dates = [
        d for d in (concept_latest, limit_latest, sector_mf_latest, stock_mf_latest)
        if d is not None
    ]
    analysis_date = min(latest_dates)

    if concept_latest > limit_latest:
        lag = _trading_day_lag(conn, limit_latest, concept_latest)
        if lag > 0:
            data_warnings.append(
                f"limit_list 滞后 {lag} 日，情绪维度使用 {limit_latest}"
            )

    concepts = conn.execute(
        """
        SELECT code, name, pct_change, hot, zt_num, main_change,
               lead_stock, lead_stock_code, lead_stock_pct
        FROM concept_board
        WHERE trade_date = ?
        ORDER BY COALESCE(hot, 0) DESC, COALESCE(zt_num, 0) DESC, COALESCE(pct_change, 0) DESC
        LIMIT ?
        """,
        [analysis_date, sector_top_n * 3],
    ).fetchall()
    if not concepts:
        return None

    sector_mf = {}
    for row in conn.execute(
        """
        SELECT code, net_amount
        FROM sector_moneyflow
        WHERE trade_date = ? AND content_type = '概念'
        """,
        [analysis_date],
    ).fetchall():
        sector_mf[row[0]] = float(row[1]) if row[1] is not None else 0.0

    limit_ladder = {"total_zt": 0}
    limit_rows = conn.execute(
        """
        SELECT limit_times, COUNT(*) AS cnt
        FROM limit_list
        WHERE trade_date = ? AND limit_type = 'U'
        GROUP BY limit_times
        ORDER BY COALESCE(limit_times, 0) DESC
        """,
        [analysis_date],
    ).fetchall()
    for lt, cnt in limit_rows:
        lt_val = int(lt) if lt is not None else 0
        if lt_val >= 5:
            key = "5连+"
        elif lt_val == 4:
            key = "4连"
        elif lt_val == 3:
            key = "3连"
        elif lt_val == 2:
            key = "2连"
        else:
            key = "首板"
        limit_ladder[key] = limit_ladder.get(key, 0) + int(cnt)
        limit_ladder["total_zt"] += int(cnt)

    mkt_mf_row = conn.execute(
        """
        SELECT net_amount, buy_elg_amount
        FROM market_moneyflow
        WHERE trade_date = ?
        """,
        [analysis_date],
    ).fetchone()
    market_moneyflow = {}
    if mkt_mf_row:
        market_moneyflow = {
            "net_amount": float(mkt_mf_row[0]) if mkt_mf_row[0] is not None else 0.0,
            "buy_elg_amount": float(mkt_mf_row[1]) if mkt_mf_row[1] is not None else 0.0,
        }

    zero_mf_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM concept_board cb
        LEFT JOIN sector_moneyflow smf
            ON smf.trade_date = cb.trade_date
           AND smf.content_type = '概念'
           AND (smf.code = cb.code OR smf.name = cb.name)
        WHERE cb.trade_date = ?
          AND COALESCE(smf.net_amount, 0) = 0
        """,
        [analysis_date],
    ).fetchone()[0]
    if zero_mf_count:
        data_warnings.append(f"{int(zero_mf_count)} 个板块无资金数据（net_amount=0）")

    # 共振分 = 资金 35% + 涨停 35% + 热度 20% + 概念涨停情绪 10%
    resonance_rows = conn.execute(
        """
        WITH base AS (
            SELECT
                cb.code,
                cb.name,
                COALESCE(cb.pct_change, 0)   AS pct_change,
                COALESCE(cb.hot, 0)          AS hot,
                COALESCE(cb.zt_num, 0)       AS zt_num,
                COALESCE(cb.main_change, 0)  AS main_change,
                COALESCE(cb.lead_stock, '')  AS lead_stock,
                COALESCE(cb.lead_stock_code, '') AS lead_stock_code,
                COALESCE(cb.lead_stock_pct, 0)   AS lead_stock_pct,
                COALESCE(smf.net_amount, 0)  AS net_amount,
                COALESCE(cl.limit_cnt, 0)    AS concept_limit_score
            FROM concept_board cb
            LEFT JOIN sector_moneyflow smf
                ON smf.trade_date = cb.trade_date
               AND smf.content_type = '概念'
               AND (smf.code = cb.code OR smf.name = cb.name)
            LEFT JOIN (
                SELECT concept_code, COUNT(DISTINCT cm.code) AS limit_cnt
                FROM concept_member cm
                JOIN limit_list ll
                  ON ll.code = cm.code
                 AND ll.trade_date = cm.trade_date
                 AND ll.limit_type = 'U'
                WHERE cm.trade_date = ?
                GROUP BY concept_code
            ) cl ON cl.concept_code = cb.code
            WHERE cb.trade_date = ?
        ),
        ranked AS (
            SELECT
                base.*,
                RANK() OVER (ORDER BY net_amount DESC) AS rk_mf,
                RANK() OVER (ORDER BY zt_num DESC)     AS rk_zt,
                RANK() OVER (ORDER BY hot DESC)        AS rk_hot,
                RANK() OVER (ORDER BY concept_limit_score DESC) AS rk_limit
            FROM base
        )
        SELECT
            code, name, pct_change, hot, zt_num, net_amount,
            lead_stock, lead_stock_code, lead_stock_pct,
            ROUND(
                (SELECT COUNT(*) FROM base) - rk_mf + 1
            ) * 0.35
          + ROUND(
                (SELECT COUNT(*) FROM base) - rk_zt + 1
            ) * 0.35
          + ROUND(
                (SELECT COUNT(*) FROM base) - rk_hot + 1
            ) * 0.2
          + ROUND(
                (SELECT COUNT(*) FROM base) - rk_limit + 1
            ) * 0.1 AS resonance_score
        FROM ranked
        ORDER BY resonance_score DESC
        LIMIT ?
        """,
        [analysis_date, analysis_date, sector_top_n],
    ).fetchall()

    mainline_sectors = []
    for row in resonance_rows:
        code, name, pct_chg, hot, zt_num, net_amt, lead, lead_code, lead_pct, score = row
        tag = "主线" if (pct_chg or 0) >= 3 else ("强势" if (pct_chg or 0) >= 2 else "观察")
        mainline_sectors.append({
            "name": name,
            "code": code,
            "change_pct": round(float(pct_chg or 0), 2),
            "hot": float(hot or 0),
            "zt_num": int(zt_num or 0),
            "net_amount": round(float(net_amt or 0) / 1e8, 4),
            "lead_stock": lead or "",
            "leader": lead or "",
            "leader_pct": round(float(lead_pct or 0), 2),
            "resonance_score": round(float(score or 0), 2),
            "strength_tag": tag,
        })

    mainline_codes = [s["code"] for s in mainline_sectors]
    strong_stocks: list[dict] = []
    concept_name_map = {s["code"]: s["name"] for s in mainline_sectors}

    if mainline_codes:
        placeholders = ",".join("?" * len(mainline_codes))
        leader_rows = conn.execute(
            f"""
            WITH candidates AS (
                SELECT code, name FROM concept_member
                WHERE trade_date = ?
                  AND concept_code IN ({placeholders})
                UNION
                SELECT code, name FROM limit_list
                WHERE trade_date = ? AND limit_type = 'U' AND COALESCE(limit_times, 0) >= 2
                UNION
                SELECT code, name FROM (
                    SELECT smf.code, sb.name,
                           ROW_NUMBER() OVER (ORDER BY smf.net_mf_amount DESC) AS rn
                    FROM stock_moneyflow smf
                    JOIN stock_basic sb ON sb.code = smf.code
                    WHERE smf.trade_date = ? AND COALESCE(smf.net_mf_amount, 0) > 0
                ) WHERE rn <= 200
            ),
            mainline_members AS (
                SELECT code, concept_code
                FROM (
                    SELECT cm.code, cm.concept_code,
                           ROW_NUMBER() OVER (
                               PARTITION BY cm.code
                               ORDER BY (cb.zt_num + cb.hot) DESC, cm.concept_code
                           ) AS rn
                    FROM concept_member cm
                    JOIN concept_board cb
                      ON cb.code = cm.concept_code
                     AND cb.trade_date = cm.trade_date
                    WHERE cm.trade_date = ?
                      AND cm.concept_code IN ({placeholders})
                ) WHERE rn = 1
            ),
            limit_up AS (
                SELECT code, limit_times, close, pct_chg
                FROM limit_list
                WHERE trade_date = ? AND limit_type = 'U' AND COALESCE(limit_times, 0) >= 2
            ),
            sd AS (
                SELECT code, trade_date, close,
                       LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
                FROM stock_daily
                WHERE trade_date <= ? AND trade_date >= (? - INTERVAL '7 days')
            ),
            sd_pct AS (
                SELECT code,
                       CASE WHEN prev_close IS NOT NULL AND prev_close != 0
                            THEN ROUND((close - prev_close) / prev_close * 100, 2)
                            ELSE 0 END AS daily_pct_chg
                FROM sd
                QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) = 1
            ),
            mf AS (
                SELECT code,
                       net_mf_amount,
                       COALESCE(buy_elg_amount, 0) - COALESCE(sell_elg_amount, 0) AS elg_net_amount,
                       COALESCE(buy_lg_amount, 0)  - COALESCE(sell_lg_amount, 0)  AS lg_net_amount,
                       COALESCE(buy_md_amount, 0)  - COALESCE(sell_md_amount, 0)  AS md_net_amount,
                       COALESCE(buy_sm_amount, 0)  - COALESCE(sell_sm_amount, 0)  AS sm_net_amount
                FROM stock_moneyflow
                WHERE trade_date = ?
            )
            SELECT
                c.code,
                c.name,
                mm.concept_code,
                COALESCE(lu.limit_times, 0)  AS limit_times,
                COALESCE(sd_pct.daily_pct_chg, COALESCE(lu.pct_chg, 0)) AS pct_chg,
                COALESCE(mf.net_mf_amount, 0) AS net_mf_amount,
                COALESCE(mf.elg_net_amount, 0) AS elg_net_amount,
                COALESCE(mf.lg_net_amount, 0)  AS lg_net_amount,
                COALESCE(mf.md_net_amount, 0)  AS md_net_amount,
                COALESCE(mf.sm_net_amount, 0)  AS sm_net_amount,
                0.0                           AS top_list_net
            FROM candidates c
            LEFT JOIN mainline_members mm ON mm.code = c.code
            LEFT JOIN limit_up lu ON lu.code = c.code
            LEFT JOIN sd_pct ON sd_pct.code = c.code
            LEFT JOIN mf ON mf.code = c.code
            WHERE COALESCE(lu.limit_times, 0) >= 2
               OR COALESCE(mf.net_mf_amount, 0) > 0
            """,
            [
                member_latest, *mainline_codes,
                analysis_date,
                analysis_date,
                member_latest, *mainline_codes,
                analysis_date,
                stock_daily_latest, stock_daily_latest,
                analysis_date,
            ],
        ).fetchall()

        if leader_rows:
            net_mf_vals = [float(r[5] or 0) for r in leader_rows]
            top_net_vals = [float(r[10] or 0) for r in leader_rows]
            mf_ranks = _rank_desc(net_mf_vals)
            tl_ranks = _rank_desc(top_net_vals)
            n = len(leader_rows)

            scored: list[dict] = []
            for i, row in enumerate(leader_rows):
                code, name, concept_code, limit_times, pct_chg, net_mf, elg_net, lg_net, md_net, sm_net, top_net = row
                rank_mf = n - mf_ranks[i] + 1
                rank_tl = n - tl_ranks[i] + 1
                score = (
                    int(limit_times or 0) * 30
                    + rank_mf * 0.4
                    + rank_tl * 0.3
                )
                # 四档净额（单位万元 → 亿元）
                elg_v = float(elg_net or 0)
                lg_v = float(lg_net or 0)
                md_v = float(md_net or 0)
                sm_v = float(sm_net or 0)
                # 主力（特大单+大单） vs 散户（小单+中单）背离标签
                main_force_net = elg_v + lg_v
                retail_net = sm_v + md_v
                if main_force_net > 0 and retail_net < 0:
                    mf_label = "主力吸筹·散户割肉"
                elif main_force_net < 0 and retail_net > 0:
                    mf_label = "主力派发·散户接盘"
                elif main_force_net > 0 and retail_net > 0:
                    mf_label = "合力净买入"
                elif main_force_net < 0 and retail_net < 0:
                    mf_label = "合力净卖出"
                else:
                    mf_label = "资金均衡"
                scored.append({
                    "code": code,
                    "name": name,
                    "concept": concept_name_map.get(concept_code, "") if concept_code else "",
                    "source": "主线成分" if concept_code else (
                        "连板涨停" if int(limit_times or 0) >= 2 else "主力净流入"
                    ),
                    "limit_times": int(limit_times or 0),
                    "change_pct": round(float(pct_chg or 0), 2),
                    "net_mf_amount": round(float(net_mf or 0) / 1e4, 2),
                    "elg_net_amount": round(elg_v / 1e4, 2),
                    "lg_net_amount": round(lg_v / 1e4, 2),
                    "md_net_amount": round(md_v / 1e4, 2),
                    "sm_net_amount": round(sm_v / 1e4, 2),
                    "mf_divergence": mf_label,
                    "top_list_net": round(float(top_net or 0) / 1e8, 4),
                    "score": round(float(score), 2),
                })

            per_track = math.ceil(strong_limit / 2)
            limit_pool = sorted(
                [s for s in scored if s["limit_times"] >= 2],
                key=lambda x: x["score"],
                reverse=True,
            )
            mf_pool = sorted(
                [s for s in scored if s["net_mf_amount"] > 0],
                key=lambda x: x["score"],
                reverse=True,
            )
            merged: list[dict] = []
            seen: set[str] = set()
            for stock in limit_pool[:per_track] + mf_pool[:per_track]:
                if stock["code"] not in seen:
                    seen.add(stock["code"])
                    merged.append(stock)
            if len(merged) < strong_limit:
                for stock in sorted(scored, key=lambda x: x["score"], reverse=True):
                    if stock["code"] not in seen:
                        seen.add(stock["code"])
                        merged.append(stock)
                        if len(merged) >= strong_limit:
                            break
            strong_stocks = merged[:strong_limit]
    # 全市场四档资金流概览（散户/中户/大户/机构净额分布）
    moneyflow_flow: dict = {}
    if stock_mf_latest is not None:
        mf_overview = conn.execute(
            """
            SELECT
                SUM(COALESCE(buy_sm_amount, 0) - COALESCE(sell_sm_amount, 0)) AS sm_net,
                SUM(COALESCE(buy_md_amount, 0) - COALESCE(sell_md_amount, 0)) AS md_net,
                SUM(COALESCE(buy_lg_amount, 0)  - COALESCE(sell_lg_amount, 0))  AS lg_net,
                SUM(COALESCE(buy_elg_amount, 0) - COALESCE(sell_elg_amount, 0)) AS elg_net,
                SUM(COALESCE(net_mf_amount, 0)) AS total_net
            FROM stock_moneyflow
            WHERE trade_date = ?
            """,
            [analysis_date],
        ).fetchone()
        if mf_overview:
            sm_n = float(mf_overview[0] or 0)
            md_n = float(mf_overview[1] or 0)
            lg_n = float(mf_overview[2] or 0)
            elg_n = float(mf_overview[3] or 0)
            total_n = float(mf_overview[4] or 0)
            main_force = elg_n + lg_n
            retail = sm_n + md_n
            if main_force > 0 and retail < 0:
                market_mf_label = "主力吸筹·散户割肉"
            elif main_force < 0 and retail > 0:
                market_mf_label = "主力派发·散户接盘"
            elif main_force > 0 and retail > 0:
                market_mf_label = "合力净买入"
            elif main_force < 0 and retail < 0:
                market_mf_label = "合力净卖出"
            else:
                market_mf_label = "资金均衡"
            moneyflow_flow = {
                "sm_net_amount": round(sm_n / 1e4, 2),
                "md_net_amount": round(md_n / 1e4, 2),
                "lg_net_amount": round(lg_n / 1e4, 2),
                "elg_net_amount": round(elg_n / 1e4, 2),
                "total_net_amount": round(total_n / 1e4, 2),
                "main_force_net": round(main_force / 1e4, 2),
                "retail_net": round(retail / 1e4, 2),
                "divergence": market_mf_label,
                "trade_date": str(analysis_date),
            }

    total_zt = limit_ladder.get("total_zt", 0)
    mkt_net = market_moneyflow.get("net_amount", 0)
    if total_zt > 50 and mkt_net > 0:
        market_phase = "情绪高潮（涨停潮+资金流入）"
    elif total_zt > 20 and mkt_net > 0:
        market_phase = "情绪回暖（涨停活跃+资金流入）"
    elif total_zt < 10 and mkt_net < 0:
        market_phase = "情绪退潮（涨停稀少+资金流出）"
    else:
        market_phase = "分化震荡（关注主线板块）"

    mainline_direction = (
        "、".join(s["name"] for s in mainline_sectors[:3])
        if mainline_sectors else "暂无明显主线"
    )

    result = {
        "market_phase": market_phase,
        "mainline_direction": mainline_direction,
        "mainline_sectors": mainline_sectors,
        "limit_ladder": limit_ladder,
        "market_moneyflow": market_moneyflow,
        "moneyflow_flow": moneyflow_flow,
        "strong_stocks": strong_stocks,
        "data_date": str(analysis_date),
        "market_snapshot": {
            "latest_date": str(concept_latest),
            "limit_latest_date": str(limit_latest),
            "member_latest_date": str(member_latest) if member_latest else None,
        },
    }
    if data_warnings:
        result["data_warnings"] = data_warnings
    return result


async def _run_sync(fn, *args):
    """同步 provider 调用丢线程池并发执行。"""
    fut = _mainline_pool.submit(fn, *args)
    return await asyncio.wrap_future(fut)


async def _run_scoring(score_fn, mover):
    """把同步的单股评分丢进线程池并发执行。"""
    fut = _mainline_pool.submit(score_fn, mover)
    try:
        return await asyncio.wrap_future(fut)
    except Exception:  # noqa: BLE001
        return None


def _read_mainline_cache() -> dict | None:
    """读取 mainline_cache 最新一行。无缓存返回 None。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT trade_date, result_json, cached_at FROM mainline_cache ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    trade_date, result_json, cached_at = row
    try:
        result = json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return None
    result["data_source"] = "offline_3d_resonance"
    result["methodology"] = "资金+情绪+逻辑三维共振"
    result["cached_at"] = str(cached_at) if cached_at else None
    result["data_date"] = str(trade_date)
    return result


async def market_mainline(args: dict) -> str:
    """识别市场主线方向与强势股票"""
    sector_top_n = int(args.get("sector_top_n", 8))
    strong_limit = int(args.get("strong_limit", 10))

    # 优先读 mainline_cache（定时任务预算结果）。
    # 缓存由定时任务以默认参数(sector_top_n=8, strong_limit=10)生成；
    # 当运行时请求的板块/股票数超出缓存已计算的范围时，直接重算而非返回截断结果。
    cached = await asyncio.to_thread(_read_mainline_cache)
    if cached is not None:
        cached_sectors = len(cached.get("mainline_sectors") or [])
        cached_stocks = len(cached.get("strong_stocks") or [])
        if sector_top_n <= cached_sectors and strong_limit <= cached_stocks:
            return json.dumps(to_json_safe(cached), ensure_ascii=False, default=str)

    # 缓存未命中或参数超出 → 离线三维度共振路径（仅 DB 查询，延迟可接受）
    db_result = await asyncio.to_thread(_score_mainline_from_db, sector_top_n, strong_limit)
    if db_result is not None:
        if cached is not None:
            db_result["cached_at"] = cached.get("cached_at")
        db_result["data_source"] = "offline_3d_resonance"
        db_result["methodology"] = "资金+情绪+逻辑三维共振"
        return json.dumps(to_json_safe(db_result), ensure_ascii=False, default=str)

    result = {
        "market_phase": "",
        "mainline_direction": "",
        "mainline_sectors": [],
        "strong_stocks": [],
        "sector_data_source": "provider",
        "methodology": "板块接口排行",
        "notes": [],
    }
    provider = get_provider()

    try:
        # 市场总体状态 — 并行拉取 4 路快照（共享 spot 缓存，并行可省串行开销）
        indices_list, stats, northbound, turnover = await asyncio.gather(
            _run_sync(safe_call, provider.get_main_indices),
            _run_sync(safe_call, provider.get_market_stats),
            _run_sync(safe_call, provider.get_northbound_flow),
            _run_sync(safe_call, provider.get_total_turnover),
        )

        indices = {idx.name: {"price": idx.price, "change_pct": idx.change_pct} for idx in indices_list}
        market_stats = {
            "total": stats.total,
            "up": stats.up,
            "down": stats.down,
            "flat": stats.flat,
            "limit_up": stats.limit_up,
            "limit_down": stats.limit_down,
        }

        result["market_phase"] = _calc_market_phase(indices, market_stats, northbound)
        result["data_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _mark_northbound_stale(northbound)
        result["market_snapshot"] = {
            "indices": indices,
            "market_stats": market_stats,
            "northbound": northbound,
            "total_turnover_yi": turnover,
        }

        # 主线板块（优先板块接口，失败则行业推断降级）
        mainline_sectors = []
        try:
            sector_tuple = await _run_sync(safe_call, provider.get_sector_rankings, sector_top_n)
            top_up, _ = sector_tuple
            for s in top_up:
                tag = "主线" if s.change_pct >= 3 else ("强势" if s.change_pct >= 2 else "观察")
                mainline_sectors.append({
                    "name": s.name,
                    "change_pct": float(s.change_pct),
                    "leader": s.leader,
                    "leader_pct": float(s.leader_pct),
                    "strength_tag": tag,
                })
        except Exception as e:
            result["sector_error"] = str(e)
            result["sector_data_source"] = "fallback_top_movers"
            result["methodology"] = "涨幅榜+行业映射推断"
            result["notes"].append("板块接口不可用，已降级为涨幅榜与行业映射推断，结果与概念题材口径可能存在偏差")
            try:
                mainline_sectors = _infer_mainline_sectors_from_movers(provider, sector_top_n)
            except Exception as fallback_e:
                result["sector_fallback_error"] = str(fallback_e)

        result["mainline_sectors"] = mainline_sectors
        result["mainline_direction"] = "、".join([s["name"] for s in mainline_sectors[:3]]) if mainline_sectors else "暂无明显主线"
        if result["sector_data_source"] == "provider":
            result["notes"].append("当前主线按数据源板块口径计算，和资讯平台按概念叙事整理的主线可能不完全一致")

        # 强势股：涨幅榜 + 技术形态过滤
        movers_tuple = await _run_sync(safe_call, provider.get_top_movers, max(strong_limit * 2, 20))
        gainers, _ = movers_tuple

        def _score_one(mover):
            """单只股票：拉日线 + 算指标 + 评分。失败返回 None。"""
            try:
                hist = safe_call(provider.get_daily_history, mover.code, None, None, 140)
                if hist is None or len(hist) < 60:
                    return None
                ind = calculate_indicators(hist, ["TREND", "VOL_MA", "NINE_TURN"])
                trend = ind.get("trend", {})
                vol_ma = ind.get("vol_ma", {})
                nine = ind.get("nine_turn", {})

                score = float(mover.change_pct)
                phase = str(trend.get("phase", ""))
                arrangement = str(trend.get("arrangement", ""))
                vol_ratio = float(vol_ma.get("vol_ratio", 0) or 0)
                signal = str(nine.get("signal", ""))

                if "上升" in phase:
                    score += 2
                if arrangement == "多头排列":
                    score += 1
                if vol_ratio >= 1.2:
                    score += 1
                if signal == "九转见顶":
                    score -= 1

                return {
                    "code": mover.code,
                    "name": mover.name,
                    "price": float(mover.price),
                    "change_pct": float(mover.change_pct),
                    "trend_phase": phase,
                    "arrangement": arrangement,
                    "vol_ratio": round(vol_ratio, 2),
                    "nine_turn_signal": signal,
                    "score": round(score, 2),
                }
            except Exception:
                # 单只股票失败不影响整体输出
                return None

        # 并发拉取 + 评分，避免 N 只股票串行阻塞（首屏最大单点延迟）
        scored = await asyncio.gather(
            *(_run_scoring(_score_one, m) for m in gainers)
        )
        candidates = [c for c in scored if c is not None]

        candidates.sort(key=lambda x: x["score"], reverse=True)
        result["strong_stocks"] = candidates[:strong_limit]

    except Exception as e:
        result["error"] = str(e)
        result["retry_same_args"] = False

    return json.dumps(to_json_safe(result), ensure_ascii=False, default=str)
