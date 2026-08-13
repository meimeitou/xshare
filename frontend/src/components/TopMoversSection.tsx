"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtPct, changeColor } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";
import { Pager } from "@/components/Pager";

interface MoverStat {
  code: string;
  name: string;
  price?: number;
  change_pct?: number;
}

const PAGE_SIZE = 6;
const MAX_PAGES = 3;
const FETCH_N = PAGE_SIZE * MAX_PAGES * 2; // 36 — enough to fill both boards × 3 pages

/** 科创/创业板: 688/300/301 prefix */
function isStarOrGEM(code: string): boolean {
  return /^(688|300|301)\d/.test(code);
}

/** 主板: 600/601/603/605/000/001/002/003 prefix */
function isMainBoard(code: string): boolean {
  return /^(60[0135]|00[0123])\d/.test(code);
}

function MoversSkeleton() {
  return (
    <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
      {[...Array(PAGE_SIZE)].map((_, i) => (
        <div
          key={i}
          className="flex items-center justify-between px-3 py-2"
          style={{ borderBottom: i < PAGE_SIZE - 1 ? "1px solid var(--border)" : "none" }}
        >
          <div className="h-4 rounded" style={{ width: "45%", background: "var(--skeleton)" }} />
          <div className="h-4 rounded" style={{ width: "15%", background: "var(--skeleton)" }} />
        </div>
      ))}
    </div>
  );
}

function MoversList({ items, page }: { items: MoverStat[]; page: number }) {
  const start = page * PAGE_SIZE;
  const slice = items.slice(start, start + PAGE_SIZE);
  return (
    <div className="surface-flat flex flex-col" style={{ borderRadius: "var(--radius)" }}>
      {slice.map((s, i) => (
        <a
          key={`${s.code}-${start + i}`}
          href={`/stock/${s.code}`}
          className="flex items-center justify-between px-3 py-2 text-sm no-underline hover:bg-[var(--bg-raised)] transition-colors"
          style={{ borderBottom: i < slice.length - 1 ? "1px solid var(--border)" : "none" }}
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

function SectionHeader({ title, color }: { title: string; color: string }) {
  return (
    <h2
      className="text-sm font-medium mb-3 flex items-center gap-1.5"
      style={{ color: "var(--text-muted)" }}
    >
      <span
        style={{
          width: "8px",
          height: "8px",
          borderRadius: "2px",
          background: color,
          display: "inline-block",
        }}
      />
      {title}
    </h2>
  );
}

/** A single board × direction list with its own pagination state */
function BoardMoverList({
  title,
  color,
  items,
}: {
  title: string;
  color: string;
  items: MoverStat[];
}) {
  const [page, setPage] = useState(0);
  const pages = Math.min(MAX_PAGES, Math.ceil(items.length / PAGE_SIZE));
  // clamp page if data shrinks
  const curPage = Math.min(page, Math.max(0, pages - 1));
  return (
    <div>
      <SectionHeader title={title} color={color} />
      {items.length > 0 ? (
        <>
          <MoversList items={items} page={curPage} />
          <Pager page={curPage} total={pages} onPage={setPage} />
        </>
      ) : (
        <MoversSkeleton />
      )}
    </div>
  );
}

export function TopMoversSection() {
  const { data, isLoading } = useSWR<{ top_gainers?: MoverStat[]; top_losers?: MoverStat[] }>(
    `/api/market/top-movers?top_n=${FETCH_N}`,
    fetcher,
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );

  const gainers = data?.top_gainers ?? [];
  const losers = data?.top_losers ?? [];

  // Split by board on the client — codes carry market suffix
  const starGainers = useMemo(() => gainers.filter((g) => isStarOrGEM(g.code)), [gainers]);
  const mainGainers = useMemo(() => gainers.filter((g) => isMainBoard(g.code)), [gainers]);
  const starLosers = useMemo(() => losers.filter((g) => isStarOrGEM(g.code)), [losers]);
  const mainLosers = useMemo(() => losers.filter((g) => isMainBoard(g.code)), [losers]);

  if (isLoading && gainers.length === 0 && losers.length === 0) {
    return (
      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <SectionHeader title="科创·涨幅榜" color="var(--up)" />
          <MoversSkeleton />
        </div>
        <div>
          <SectionHeader title="主板·涨幅榜" color="var(--up)" />
          <MoversSkeleton />
        </div>
        <div>
          <SectionHeader title="科创·跌幅榜" color="var(--down)" />
          <MoversSkeleton />
        </div>
        <div>
          <SectionHeader title="主板·跌幅榜" color="var(--down)" />
          <MoversSkeleton />
        </div>
      </section>
    );
  }

  if (gainers.length === 0 && losers.length === 0) return null;

  return (
    <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <BoardMoverList title="科创·涨幅榜" color="var(--up)" items={starGainers} />
      <BoardMoverList title="主板·涨幅榜" color="var(--up)" items={mainGainers} />
      <BoardMoverList title="科创·跌幅榜" color="var(--down)" items={starLosers} />
      <BoardMoverList title="主板·跌幅榜" color="var(--down)" items={mainLosers} />
    </section>
  );
}
