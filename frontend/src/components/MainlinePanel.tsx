"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";
import { Pager } from "@/components/Pager";

interface MainlineStock {
  code: string;
  name: string;
  change_pct?: number;
  score?: number;
  trend_phase?: string;
  arrangement?: string;
  vol_ratio?: number;
  nine_turn_signal?: string;
  price?: number;
  net_mf_amount?: number;
  elg_net_amount?: number;
  concept?: string;
  limit_times?: number;
}

interface MainlineSector {
  name: string;
  change_pct?: number;
  leader?: string;
  leader_pct?: number;
  strength_tag?: string;
  net_amount?: number;
  zt_num?: number;
  resonance_score?: number;
}

interface MainlineResponse {
  market_phase?: string;
  mainline_direction?: string;
  mainline_sectors?: MainlineSector[];
  strong_stocks?: MainlineStock[];
  data_date?: string;
  cached_at?: string;
}

function SectorsList({ sectors }: { sectors: MainlineSector[] }) {
  return (
    <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
      {sectors.slice(0, 5).map((s, i) => (
        <div
          key={i}
          className="flex items-center justify-between px-3 py-2 text-sm"
          style={{ borderBottom: i < 4 ? "1px solid var(--border)" : "none" }}
        >
          <div className="flex flex-col" style={{ minWidth: 0, flex: 1 }}>
            <span style={{ color: "var(--text)" }}>{s.name}</span>
            {s.strength_tag && (
              <span className="text-xs" style={{ color: "var(--text-dim)" }}>{s.strength_tag}</span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {s.net_amount != null && (
              <span className="mono text-xs" style={{ color: s.net_amount >= 0 ? "var(--up)" : "var(--down)" }}>
                {s.net_amount >= 0 ? "+" : ""}{s.net_amount.toFixed(2)}亿
              </span>
            )}
            <span className="mono" style={{ color: (s.change_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>
              {fmtPct(s.change_pct)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

/** 科创板代码以 688 开头；其余视为主板（含创业板/北交所，用户暂不细分）。 */
function isStarMarket(code: string): boolean {
  return code.startsWith("688");
}

const STOCK_PAGE_SIZE = 6;
const STOCK_MAX_PAGES = 3;


function StrongStocksList({ stocks }: { stocks: MainlineStock[] }) {
  const [page, setPage] = useState(0);
  const pages = Math.min(STOCK_MAX_PAGES, Math.ceil(stocks.length / STOCK_PAGE_SIZE));
  const curPage = Math.min(page, Math.max(0, pages - 1));
  const start = curPage * STOCK_PAGE_SIZE;
  const slice = stocks.slice(start, start + STOCK_PAGE_SIZE);

  return (
    <>
      <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
        {slice.map((s, i) => (
          <a
            key={`${s.code}-${start + i}`}
            href={`/stock/${s.code}`}
            className="flex items-center justify-between px-3 py-2 text-sm no-underline hover:bg-[var(--bg-raised)] transition-colors"
            style={{ borderBottom: i < slice.length - 1 ? "1px solid var(--border)" : "none" }}
          >
            <div className="flex flex-col" style={{ minWidth: 0, flex: 1 }}>
              <span style={{ color: "var(--text)" }}>
                {s.name}
                <span className="ml-1.5 mono text-xs" style={{ color: "var(--text-dim)" }}>
                  {s.code}
                </span>
              </span>
              {s.concept && (
                <span className="text-xs" style={{ color: "var(--text-dim)" }}>{s.concept}</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {s.elg_net_amount != null && (
                <span className="mono text-xs" style={{ color: s.elg_net_amount >= 0 ? "var(--up)" : "var(--down)" }} title="超大单净额">
                  超{s.elg_net_amount >= 0 ? "+" : ""}{s.elg_net_amount.toFixed(2)}亿
                </span>
              )}
              {s.net_mf_amount != null && (
                <span className="mono text-xs" style={{ color: s.net_mf_amount >= 0 ? "var(--up)" : "var(--down)" }} title="主力净额(大单+超大单)">
                  主{s.net_mf_amount >= 0 ? "+" : ""}{s.net_mf_amount.toFixed(2)}亿
                </span>
              )}
              <span className="mono" style={{ color: (s.change_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>
                {fmtPct(s.change_pct)}
              </span>
            </div>
          </a>
        ))}
      </div>
      <Pager page={curPage} total={pages} onPage={setPage} />
    </>
  );
}

function PanelSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
          主线板块
        </h3>
        <Skeleton style={{ height: "160px" }} />
      </div>
      <div>
        <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
          强势股
        </h3>
        <Skeleton style={{ height: "220px" }} />
      </div>
    </div>
  );
}

export function MainlinePanel() {
  // 强势股（最重，含 N+1 日线 + 指标计算）单独拉取，独立于板块快照。
  const { data, isLoading } = useSWR<MainlineResponse>(
    "/api/market/mainline-stocks?strong_limit=18&sector_top_n=5",
    fetcher,
    { refreshInterval: 120_000, revalidateOnFocus: false, keepPreviousData: true },
  );

  if (isLoading && !data) {
    return <PanelSkeleton />;
  }

  const sectors = data?.mainline_sectors ?? [];
  const stocks = data?.strong_stocks ?? [];

  if (sectors.length === 0 && stocks.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-dim)" }}>
        暂无主线数据
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {data?.market_phase && (
        <p className="text-xs" style={{ color: "var(--accent)" }}>
          {data.market_phase}
        </p>
      )}
      {data?.data_date && (
        <p className="text-xs mono" style={{ color: "var(--text-dim)" }}>
          数据日期 {data.data_date}
          {data?.cached_at && <span style={{ color: "var(--text-muted)" }}> · 计算于 {data.cached_at}</span>}
        </p>
      )}
      {sectors.length > 0 && (
        <div>
          <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
            主线板块
          </h3>
          <SectorsList sectors={sectors} />
        </div>
      )}
      {stocks.length > 0 && (() => {
        const main = stocks.filter((s) => !isStarMarket(s.code));
        const star = stocks.filter((s) => isStarMarket(s.code));
        return (
          <>
            {main.length > 0 && (
              <div>
                <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
                  强势股 · 主板
                </h3>
                <StrongStocksList stocks={main} />
              </div>
            )}
            {star.length > 0 && (
              <div>
                <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
                  强势股 · 科创板
                </h3>
                <StrongStocksList stocks={star} />
              </div>
            )}
          </>
        );
      })()}
    </div>
  );
}
