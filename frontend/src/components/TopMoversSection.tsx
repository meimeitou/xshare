"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtPct, changeColor } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";

interface MoverStat {
  code: string;
  name: string;
  price?: number;
  change_pct?: number;
}

function MoversSkeleton() {
  return (
    <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
      {[...Array(6)].map((_, i) => (
        <div
          key={i}
          className="flex items-center justify-between px-3 py-2"
          style={{ borderBottom: i < 5 ? "1px solid var(--border)" : "none" }}
        >
          <Skeleton style={{ height: "14px", width: "45%" }} />
          <Skeleton style={{ height: "14px", width: "52px" }} />
        </div>
      ))}
    </div>
  );
}

function MoversList({ items }: { items: MoverStat[] }) {
  return (
    <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
      {items.slice(0, 6).map((s, i) => (
        <a
          key={i}
          href={`/stock/${s.code}`}
          className="flex items-center justify-between px-3 py-2 text-sm no-underline hover:bg-[var(--bg-raised)] transition-colors"
          style={{ borderBottom: i < 5 ? "1px solid var(--border)" : "none" }}
        >
          <span style={{ color: "var(--text)" }}>
            {s.name}
            <span className="ml-1.5 mono text-xs" style={{ color: "var(--text-dim)" }}>
              {s.code}
            </span>
          </span>
          <span className={`mono text-sm ${changeColor(s.change_pct)}`}>{fmtPct(s.change_pct)}</span>
        </a>
      ))}
    </div>
  );
}

export function TopMoversSection() {
  const { data, isLoading } = useSWR<{ top_gainers?: MoverStat[]; top_losers?: MoverStat[] }>(
    "/api/market/top-movers?top_n=6",
    fetcher,
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );

  const gainers = data?.top_gainers ?? [];
  const losers = data?.top_losers ?? [];

  if (isLoading && gainers.length === 0 && losers.length === 0) {
    return (
      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <h2
            className="text-sm font-medium mb-3 flex items-center gap-1.5"
            style={{ color: "var(--text-muted)" }}
          >
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "2px",
                background: "var(--up)",
                display: "inline-block",
              }}
            />
            涨幅榜
          </h2>
          <MoversSkeleton />
        </div>
        <div>
          <h2
            className="text-sm font-medium mb-3 flex items-center gap-1.5"
            style={{ color: "var(--text-muted)" }}
          >
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "2px",
                background: "var(--down)",
                display: "inline-block",
              }}
            />
            跌幅榜
          </h2>
          <MoversSkeleton />
        </div>
      </section>
    );
  }

  if (gainers.length === 0 && losers.length === 0) return null;

  return (
    <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
      {gainers.length > 0 && (
        <div>
          <h2
            className="text-sm font-medium mb-3 flex items-center gap-1.5"
            style={{ color: "var(--text-muted)" }}
          >
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "2px",
                background: "var(--up)",
                display: "inline-block",
              }}
            />
            涨幅榜
          </h2>
          <MoversList items={gainers} />
        </div>
      )}
      {losers.length > 0 && (
        <div>
          <h2
            className="text-sm font-medium mb-3 flex items-center gap-1.5"
            style={{ color: "var(--text-muted)" }}
          >
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "2px",
                background: "var(--down)",
                display: "inline-block",
              }}
            />
            跌幅榜
          </h2>
          <MoversList items={losers} />
        </div>
      )}
    </section>
  );
}
