"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";

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
}

interface MainlineSector {
  name: string;
  change_pct?: number;
  leader?: string;
  leader_pct?: number;
  strength_tag?: string;
}

interface MainlineResponse {
  market_phase?: string;
  mainline_direction?: string;
  mainline_sectors?: MainlineSector[];
  strong_stocks?: MainlineStock[];
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
          <span style={{ color: "var(--text)" }}>{s.name}</span>
          <span className="mono" style={{ color: (s.change_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>
            {fmtPct(s.change_pct)}
          </span>
        </div>
      ))}
    </div>
  );
}

function StrongStocksList({ stocks }: { stocks: MainlineStock[] }) {
  return (
    <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
      {stocks.slice(0, 8).map((s, i) => (
        <a
          key={i}
          href={`/stock/${s.code}`}
          className="flex items-center justify-between px-3 py-2 text-sm no-underline hover:bg-[var(--bg-raised)] transition-colors"
          style={{ borderBottom: i < 7 ? "1px solid var(--border)" : "none" }}
        >
          <span style={{ color: "var(--text)" }}>
            {s.name}
            <span className="ml-1.5 mono text-xs" style={{ color: "var(--text-dim)" }}>
              {s.code}
            </span>
          </span>
          <span className="mono text-xs" style={{ color: (s.change_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>
            {fmtPct(s.change_pct)}
          </span>
        </a>
      ))}
    </div>
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
    "/api/market/mainline-stocks?strong_limit=8&sector_top_n=5",
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
      {sectors.length > 0 && (
        <div>
          <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
            主线板块
          </h3>
          <SectorsList sectors={sectors} />
        </div>
      )}
      {stocks.length > 0 && (
        <div>
          <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
            强势股
          </h3>
          <StrongStocksList stocks={stocks} />
        </div>
      )}
    </div>
  );
}
