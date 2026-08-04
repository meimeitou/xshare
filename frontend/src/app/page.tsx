"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { StatTile } from "@/components/StatTile";
import { Skeleton } from "@/components/Skeleton";
import { ArrowRight } from "@phosphor-icons/react";
import { SearchBar } from "@/components/SearchBar";
import { SectorsSection } from "@/components/SectorsSection";
import { MainlinePanel } from "@/components/MainlinePanel";
import { TopMoversSection } from "@/components/TopMoversSection";

interface IndexStat {
  name: string;
  code?: string;
  current?: number;
  change?: number;
  change_pct?: number;
  volume?: number;
  amount?: number;
  price?: number;
}
interface MarketSentiment {
  advance?: number;
  decline?: number;
  volume?: number;
  amount?: number;
  north_flow?: number;
}
interface OverviewData {
  indices?: IndexStat[] | Record<string, IndexStat>;
  market_stats?: {
    total?: number;
    up?: number;
    down?: number;
    flat?: number;
    limit_up?: number;
    limit_down?: number;
  };
  total_turnover_yi?: number;
  northbound?: { total?: number; sh_connect?: number; sz_connect?: number; date?: string };
  snapshot_time?: string;
}

function asArray<T>(value: unknown, mapItem?: (item: unknown) => T): T[] {
  const list = Array.isArray(value)
    ? value
    : value && typeof value === "object"
      ? Object.values(value as Record<string, unknown>)
      : [];
  return mapItem ? list.map(mapItem) : (list as T[]);
}

function IndexCard({ stat }: { stat: IndexStat }) {
  const pct = stat.change_pct ?? 0;
  const isUp = pct >= 0;
  const color = isUp ? "var(--up)" : "var(--down)";
  return (
    <div
      className="surface flex flex-col gap-2 p-4"
      style={{
        border: `1px solid ${isUp ? "color-mix(in srgb,var(--up) 26%,var(--border))" : "color-mix(in srgb,var(--down) 26%,var(--border))"}`,
        borderRadius: "var(--radius)",
      }}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs" style={{ color: "var(--text-dim)" }}>
          {stat.name}
        </span>
        <span
          className="text-xs mono px-1.5 py-0.5 rounded"
          style={{
            background: isUp
              ? "color-mix(in srgb,var(--up) 12%,transparent)"
              : "color-mix(in srgb,var(--down) 12%,transparent)",
            color,
          }}
        >
          {fmtPct(pct)}
        </span>
      </div>
      <div className="mono text-2xl font-bold" style={{ color: "var(--text)" }}>
        {stat.current?.toFixed(2) ?? "--"}
      </div>
      <div className="text-xs mono" style={{ color: "var(--text-muted)" }}>
        {stat.change != null ? `${stat.change >= 0 ? "+" : ""}${stat.change.toFixed(2)}` : ""}
      </div>
    </div>
  );
}

function IndicesSection() {
  const { data: overview, isLoading: loadingO } = useSWR<OverviewData>(
    "/api/market/overview",
    fetcher,
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );

  const indices = asArray<IndexStat>(overview?.indices, (item) => {
    const stat = item as Partial<IndexStat> & { price?: number };
    return {
      name: stat.name ?? "--",
      code: stat.code,
      current: stat.current ?? stat.price,
      change: stat.change,
      change_pct: stat.change_pct,
      volume: stat.volume,
      amount: stat.amount,
    };
  });

  if (loadingO && indices.length === 0) {
    return (
      <section>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} style={{ height: "96px" }} />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {indices.slice(0, 4).map((idx, i) => (
          <IndexCard key={i} stat={idx} />
        ))}
      </div>
      {indices.length === 0 && (
        <div
          className="surface flex flex-col items-center gap-3 py-16"
          style={{ color: "var(--text-dim)" }}
        >
          <p className="text-sm">无法获取市场数据，请确认 API 服务已启动</p>
          <a href="/sync" className="flex items-center gap-1.5 text-sm no-underline" style={{ color: "var(--accent)" }}>
            前往同步管理 <ArrowRight size={14} />
          </a>
        </div>
      )}
    </section>
  );
}

function SentimentSection() {
  const { data: overview } = useSWR<OverviewData>("/api/market/overview", fetcher, {
    refreshInterval: 60_000,
    revalidateOnFocus: false,
  });

  const stats = overview?.market_stats;
  const turnover = overview?.total_turnover_yi;
  const northbound = overview?.northbound;

  if (!stats && turnover == null && !northbound) return null;

  return (
    <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <StatTile label="上涨家数" value={String(stats?.up ?? "-")} valueColor="var(--up)" />
      <StatTile label="下跌家数" value={String(stats?.down ?? "-")} valueColor="var(--down)" />
      <StatTile label="总成交额" value={turnover != null ? `¥${turnover.toFixed(0)}亿` : "-"} />
      <StatTile
        label="北向资金"
        value={northbound?.total != null ? `${northbound.total >= 0 ? "+" : ""}${northbound.total.toFixed(2)}亿` : "-"}
        valueColor={
          northbound?.total != null ? (northbound.total >= 0 ? "var(--up)" : "var(--down)") : undefined
        }
      />
    </section>
  );
}

export default function MarketPage() {
  const { data: overview } = useSWR<OverviewData>("/api/market/overview", fetcher, {
    refreshInterval: 60_000,
    revalidateOnFocus: false,
  });

  return (
    <div className="flex flex-col gap-6 md:gap-7">
      <div className="surface px-5 py-4 md:px-6 md:py-5 flex items-center justify-between flex-wrap gap-3">
        <div className="space-y-1">
          <p className="mono text-[11px] tracking-[0.08em]" style={{ color: "var(--text-dim)" }}>
            MARKET OVERVIEW
          </p>
          <h1
            className="text-xl md:text-2xl font-semibold tracking-tight"
            style={{ color: "var(--text)" }}
          >
            中国市场全景监控
          </h1>
          {overview?.snapshot_time && (
            <p className="text-xs mono" style={{ color: "var(--text-dim)" }}>
              更新时间 {overview.snapshot_time}
            </p>
          )}
        </div>
        <SearchBar />
      </div>

      {/* 指数卡片（首屏视觉主体，走 overview，最高优先级） */}
      <IndicesSection />

      {/* 涨跌统计/成交额/北向（走 overview，与指数共享请求） */}
      <SentimentSection />

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <h2 className="text-sm font-medium mb-3" style={{ color: "var(--text-muted)" }}>
            板块涨跌
          </h2>
          {/* 板块独立 SWR，不被 overview 其它字段拖累 */}
          <SectorsSection />
        </div>
        <div>
          <h2 className="text-sm font-medium mb-3" style={{ color: "var(--text-muted)" }}>
            主线方向
          </h2>
          {/* 主线（含 N+1 日线+指标计算，最重）独立 SWR，延迟加载 */}
          <MainlinePanel />
        </div>
      </section>

      {/* 涨跌幅榜独立 SWR，自带骨架 */}
      <TopMoversSection />
    </div>
  );
}
