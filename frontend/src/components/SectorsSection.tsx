"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";

interface SectorStat {
  name: string;
  change_pct?: number;
  leader?: string;
  leader_pct?: number;
  count?: number;
}

interface SectorsResponse {
  sector_top_up?: SectorStat[];
  sector_top_down?: SectorStat[];
}

function SectorBento({ sectors }: { sectors: SectorStat[] }) {
  return (
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))" }}
    >
      {sectors.slice(0, 12).map((s, i) => {
        const pct = s.change_pct ?? 0;
        const isUp = pct >= 0;
        const intensity = Math.min(Math.abs(pct) / 5, 1);
        const bg = isUp
          ? `color-mix(in srgb, var(--up) ${Math.round(intensity * 18)}%, var(--bg-panel))`
          : `color-mix(in srgb, var(--down) ${Math.round(intensity * 18)}%, var(--bg-panel))`;
        return (
          <div
            key={i}
            className="flex flex-col gap-1 p-3 rounded cursor-default"
            style={{
              background: bg,
              border: "1px solid color-mix(in srgb, var(--border) 78%, transparent)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <span className="text-xs truncate" style={{ color: "var(--text)" }}>
              {s.name}
            </span>
            <span className="mono text-sm font-semibold" style={{ color: isUp ? "var(--up)" : "var(--down)" }}>
              {fmtPct(pct)}
            </span>
            {s.leader && (
              <span className="text-[11px] truncate" style={{ color: "var(--text-dim)" }}>
                {s.leader}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function SectorsSection() {
  const { data, isLoading } = useSWR<SectorsResponse>(
    "/api/market/sectors?top_n=12",
    fetcher,
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );
  const sectors = data?.sector_top_up ?? [];

  if (isLoading && sectors.length === 0) {
    return (
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))" }}>
        {[...Array(12)].map((_, i) => (
          <Skeleton key={i} style={{ height: "72px" }} />
        ))}
      </div>
    );
  }

  if (sectors.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-dim)" }}>
        暂无板块数据
      </p>
    );
  }

  return <SectorBento sectors={sectors} />;
}
