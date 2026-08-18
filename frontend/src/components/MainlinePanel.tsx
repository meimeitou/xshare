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
  lg_net_amount?: number;
  md_net_amount?: number;
  sm_net_amount?: number;
  mf_divergence?: string;
  source?: string;
  concept?: string;
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

interface MoneyflowFlow {
  sm_net_amount?: number;
  md_net_amount?: number;
  lg_net_amount?: number;
  elg_net_amount?: number;
  total_net_amount?: number;
  main_force_net?: number;
  retail_net?: number;
  divergence?: string;
  trade_date?: string;
}

interface MainlineResponse {
  market_phase?: string;
  mainline_direction?: string;
  mainline_sectors?: MainlineSector[];
  strong_stocks?: MainlineStock[];
  moneyflow_flow?: MoneyflowFlow;
  data_date?: string;
  cached_at?: string;
  data_warnings?: string[];
  market_snapshot?: {
    latest_date?: string;
    limit_latest_date?: string;
    member_latest_date?: string;
  };
}

function SectorBlocks({
  sectors,
  selected,
  onSelect,
}: {
  sectors: MainlineSector[];
  selected: string | null;
  onSelect: (name: string | null) => void;
}) {
  return (
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}
    >
      <button
        onClick={() => onSelect(null)}
        className="flex flex-col gap-1 p-2.5 rounded text-left transition-colors"
        style={{
          background: selected === null ? "color-mix(in srgb, var(--accent) 14%, var(--bg-panel))" : "var(--bg-panel)",
          border: selected === null ? "1px solid color-mix(in srgb, var(--accent) 40%, var(--border))" : "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
        }}
      >
        <span className="text-xs" style={{ color: selected === null ? "var(--accent-strong)" : "var(--text-dim)" }}>
          全部
        </span>
        <span className="mono text-sm font-semibold" style={{ color: "var(--text-muted)" }}>
          {sectors.length}板块
        </span>
      </button>
      {sectors.map((s, i) => {
        const pct = s.change_pct ?? 0;
        const isUp = pct >= 0;
        const isActive = selected === s.name;
        const intensity = Math.min(Math.abs(pct) / 5, 1);
        const bg = isUp
          ? `color-mix(in srgb, var(--up) ${Math.round(intensity * 18)}%, var(--bg-panel))`
          : `color-mix(in srgb, var(--down) ${Math.round(intensity * 18)}%, var(--bg-panel))`;
        return (
          <button
            key={i}
            onClick={() => onSelect(isActive ? null : s.name)}
            className="flex flex-col gap-1 p-2.5 rounded text-left transition-colors"
            style={{
              background: isActive ? `color-mix(in srgb, var(--accent) 20%, ${bg})` : bg,
              border: isActive
                ? "1px solid var(--accent)"
                : "1px solid color-mix(in srgb, var(--border) 78%, transparent)",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
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
                {s.leader} {s.leader_pct != null && fmtPct(s.leader_pct)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
/** 板块分类：688/689 科创板，300/301 创业板，其余主板（含北交所）。 */
function isStarMarket(code: string): boolean {
  return code.startsWith("688");
}

function isChinext(code: string): boolean {
  return code.startsWith("300") || code.startsWith("301");
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
              {s.concept ? (
                <span className="text-xs" style={{ color: "var(--text-dim)" }}>{s.concept}</span>
              ) : s.source ? (
                <span className="text-xs px-1 rounded" style={{ color: "var(--text-dim)", border: "1px solid var(--border)" }}>{s.source}</span>
              ) : null}
            </div>
            <div className="flex flex-col items-end gap-0.5">
              <div className="flex items-center gap-2">
                {s.elg_net_amount != null && (
                  <span className="mono text-xs" style={{ color: s.elg_net_amount >= 0 ? "var(--up)" : "var(--down)" }} title="特大单净额（机构）">
                    机{s.elg_net_amount >= 0 ? "+" : ""}{s.elg_net_amount.toFixed(2)}
                  </span>
                )}
                {s.lg_net_amount != null && (
                  <span className="mono text-xs" style={{ color: s.lg_net_amount >= 0 ? "var(--up)" : "var(--down)" }} title="大单净额（大户/游资）">
                    大{s.lg_net_amount >= 0 ? "+" : ""}{s.lg_net_amount.toFixed(2)}
                  </span>
                )}
                {s.md_net_amount != null && (
                  <span className="mono text-xs" style={{ color: s.md_net_amount >= 0 ? "var(--up)" : "var(--down)" }} title="中单净额（中户）">
                    中{s.md_net_amount >= 0 ? "+" : ""}{s.md_net_amount.toFixed(2)}
                  </span>
                )}
                {s.sm_net_amount != null && (
                  <span className="mono text-xs" style={{ color: s.sm_net_amount >= 0 ? "var(--up)" : "var(--down)" }} title="小单净额（散户）">
                    散{s.sm_net_amount >= 0 ? "+" : ""}{s.sm_net_amount.toFixed(2)}
                  </span>
                )}
                <span className="mono" style={{ color: (s.change_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>
                  {fmtPct(s.change_pct)}
                </span>
              </div>
              {s.mf_divergence && (
                <span className="text-xs" style={{
                  color: s.mf_divergence.includes("吸筹") ? "var(--up)"
                    : s.mf_divergence.includes("派发") ? "var(--down)"
                    : "var(--text-dim)",
                }}>
                  {s.mf_divergence}
                </span>
              )}
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

  const [selectedSector, setSelectedSector] = useState<string | null>(null);

  if (isLoading && !data) {
    return <PanelSkeleton />;
  }

  const sectors = data?.mainline_sectors ?? [];
  const stocks = data?.strong_stocks ?? [];
  const filteredStocks = selectedSector
    ? stocks.filter((s) => s.concept === selectedSector)
    : stocks;
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
      {data?.market_snapshot?.limit_latest_date &&
        data.data_date &&
        data.market_snapshot.limit_latest_date < data.data_date.slice(0, 10) && (
          <p className="text-xs" style={{ color: "var(--accent)" }}>
            涨停数据截至 {data.market_snapshot.limit_latest_date}
          </p>
      )}
      {data?.data_warnings?.map((w, i) => (
        <p key={i} className="text-xs" style={{ color: "var(--text-dim)" }}>
          {w}
        </p>
      ))}
      {data?.moneyflow_flow && (() => {
        const mf = data.moneyflow_flow;
        const tier = (label: string, val?: number, title?: string) =>
          val != null ? (
            <span className="mono text-xs" style={{ color: val >= 0 ? "var(--up)" : "var(--down)" }} title={title}>
              {label}{val >= 0 ? "+" : ""}{val.toFixed(1)}
            </span>
          ) : null;
        const divColor = mf.divergence?.includes("吸筹") ? "var(--up)"
          : mf.divergence?.includes("派发") ? "var(--down)"
          : "var(--text-dim)";
        return (
          <div className="surface-flat flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2" style={{ borderRadius: "var(--radius)" }}>
            <span className="text-xs" style={{ color: "var(--text-dim)" }}>资金流</span>
            {tier("散", mf.sm_net_amount, "小单净额（散户）")}
            {tier("中", mf.md_net_amount, "中单净额（中户）")}
            {tier("大", mf.lg_net_amount, "大单净额（大户/游资）")}
            {tier("机", mf.elg_net_amount, "特大单净额（机构）")}
            {tier("合力", mf.total_net_amount, "全市场净流入额")}
            {mf.divergence && (
              <span className="text-xs px-1.5 py-0.5 rounded" style={{ color: divColor, border: `1px solid ${divColor}` }}>
                {mf.divergence}
              </span>
            )}
          </div>
        );
      })()}
      {sectors.length > 0 && (
        <div>
          <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
            主线板块
          </h3>
          <SectorBlocks sectors={sectors} selected={selectedSector} onSelect={setSelectedSector} />
        </div>
      )}
      {filteredStocks.length > 0 ? (() => {
        const main = filteredStocks.filter((s) => !isStarMarket(s.code) && !isChinext(s.code));
        const chinext = filteredStocks.filter((s) => isChinext(s.code));
        const star = filteredStocks.filter((s) => isStarMarket(s.code));
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
            {chinext.length > 0 && (
              <div>
                <h3 className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
                  强势股 · 创业板
                </h3>
                <StrongStocksList stocks={chinext} />
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
      })() : stocks.length > 0 && (
        <p className="text-xs" style={{ color: "var(--text-dim)" }}>
          「{selectedSector}」暂无入选强势股
        </p>
      )}
    </div>
  );
}
