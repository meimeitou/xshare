"use client";

import { use, useState, useMemo } from "react";
import useSWR from "swr";
import dynamic from "next/dynamic";
import { fetcher } from "@/lib/api";
import { fmtPct, fmtVol, fmtAmount, changeColor } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";
import { StatTile } from "@/components/StatTile";
import { ArrowLeft, Newspaper } from "@phosphor-icons/react";
import Link from "next/link";

const StockChart = dynamic(
  () =>
    import("@/components/StockChart").then((m) => ({ default: m.StockChart })),
  { ssr: false },
);

/* ------ Types -------------------------------------------------------------------------------------------------------------------------------- */
interface Quote {
  code: string;
  name?: string;
  current?: number;
  open?: number;
  high?: number;
  low?: number;
  prev_close?: number;
  change?: number;
  change_pct?: number;
  volume?: number;
  amount?: number;
  turnover?: number;
  pe?: number;
  pb?: number;
}
interface IndicatorsData {
  code: string;
  period?: string;
  bars?: {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }[];
  MA?: { date: string; value: number }[][];
  MA_periods?: number[];
  MACD?: { date: string; macd: number; signal: number; histogram: number }[];
  RSI?: { date: string; value: number }[];
  KDJ?: { date: string; k: number; d: number; j: number }[];
  BOLL?: { date: string; upper: number; middle: number; lower: number }[];
}
interface Fundamentals {
  code: string;
  name?: string;
  pe?: number;
  pb?: number;
  roe?: number;
  revenue_yoy?: number;
  net_profit_yoy?: number;
}
interface NewsItem {
  id: string;
  title: string;
  publish_time?: string;
  tags?: string[];
}

/* ------ Helpers ---------------------------------------------------------------------------------------------------------------------------- */
const INDICATOR_TABS = ["MACD", "RSI", "KDJ", "BOLL"] as const;
const PERIOD_TABS = ["daily", "weekly", "monthly"] as const;
const PERIOD_LABELS = { daily: "日K", weekly: "周K", monthly: "月K" };

const MA_COLORS = ["#facc15", "#f97316", "#a78bfa", "#38bdf8"];

function QuoteHeader({ quote, code }: { quote: Quote; code: string }) {
  const pct = quote.change_pct ?? 0;
  const isUp = pct >= 0;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-3 flex-wrap">
        <span
          className="mono text-4xl font-bold"
          style={{ color: "var(--text)" }}
        >
          {quote.current?.toFixed(2) ?? "--"}
        </span>
        <span
          className="mono text-lg px-2 py-0.5 rounded"
          style={{
            color: isUp ? "var(--up)" : "var(--down)",
            background: isUp
              ? "color-mix(in srgb,var(--up) 10%,transparent)"
              : "color-mix(in srgb,var(--down) 10%,transparent)",
          }}
        >
          {fmtPct(pct)}
        </span>
        {quote.change != null && (
          <span
            className="mono text-lg"
            style={{ color: isUp ? "var(--up)" : "var(--down)" }}
          >
            {quote.change >= 0 ? "+" : ""}
            {quote.change.toFixed(2)}
          </span>
        )}
      </div>
      <div
        className="flex flex-wrap gap-4 text-xs mono"
        style={{ color: "var(--text-muted)" }}
      >
        {quote.open != null && (
          <span>
            开盘{" "}
            <span style={{ color: "var(--text)" }}>
              {quote.open.toFixed(2)}
            </span>
          </span>
        )}
        {quote.high != null && (
          <span>
            最高{" "}
            <span style={{ color: "var(--up)" }}>{quote.high.toFixed(2)}</span>
          </span>
        )}
        {quote.low != null && (
          <span>
            最低{" "}
            <span style={{ color: "var(--down)" }}>{quote.low.toFixed(2)}</span>
          </span>
        )}
        {quote.prev_close != null && (
          <span>
            昨收{" "}
            <span style={{ color: "var(--text)" }}>
              {quote.prev_close.toFixed(2)}
            </span>
          </span>
        )}
        {quote.volume != null && (
          <span>
            成交量{" "}
            <span style={{ color: "var(--text)" }}>{fmtVol(quote.volume)}</span>
          </span>
        )}
        {quote.amount != null && (
          <span>
            成交额{" "}
            <span style={{ color: "var(--text)" }}>
              {fmtAmount(quote.amount)}
            </span>
          </span>
        )}
        {quote.turnover != null && (
          <span>
            换手率{" "}
            <span style={{ color: "var(--text)" }}>
              {quote.turnover.toFixed(2)}%
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

function IndicatorChart({ data, tab }: { data: IndicatorsData; tab: string }) {
  if (tab === "MACD" && data.MACD?.length) {
    const bars = data.MACD.slice(-60);
    const max = Math.max(...bars.map((b) => Math.abs(b.histogram)));
    return (
      <div className="overflow-x-auto">
        <div
          className="flex items-end gap-0.5"
          style={{ height: "80px", minWidth: `${bars.length * 6}px` }}
        >
          {bars.map((b, i) => {
            const h = max > 0 ? (Math.abs(b.histogram) / max) * 76 : 0;
            return (
              <div
                key={i}
                style={{
                  flex: "1",
                  height: `${h}px`,
                  minHeight: "1px",
                  background: b.histogram >= 0 ? "var(--up)" : "var(--down)",
                  opacity: 0.8,
                }}
              />
            );
          })}
        </div>
        <div
          className="flex gap-4 text-xs mono mt-2"
          style={{ color: "var(--text-muted)" }}
        >
          {data.MACD.length > 0 && (
            <>
              <span>
                MACD{" "}
                <span style={{ color: "var(--accent)" }}>
                  {data.MACD.at(-1)!.macd.toFixed(3)}
                </span>
              </span>
              <span>
                Signal{" "}
                <span style={{ color: "#facc15" }}>
                  {data.MACD.at(-1)!.signal.toFixed(3)}
                </span>
              </span>
              <span>
                Hist{" "}
                <span
                  style={{
                    color:
                      data.MACD.at(-1)!.histogram >= 0
                        ? "var(--up)"
                        : "var(--down)",
                  }}
                >
                  {data.MACD.at(-1)!.histogram.toFixed(3)}
                </span>
              </span>
            </>
          )}
        </div>
      </div>
    );
  }
  if (tab === "RSI" && data.RSI?.length) {
    const latest = data.RSI.at(-1);
    const v = latest?.value ?? 50;
    const color =
      v >= 70 ? "var(--down)" : v <= 30 ? "var(--up)" : "var(--accent)";
    return (
      <div className="flex items-center gap-4">
        <div className="flex flex-col gap-1">
          <span className="mono text-3xl font-bold" style={{ color }}>
            {v.toFixed(1)}
          </span>
          <span className="text-xs" style={{ color: "var(--text-dim)" }}>
            RSI(14) - {v >= 70 ? "超买" : v <= 30 ? "超卖" : "中性"}
          </span>
        </div>
        <div
          className="flex-1 h-2 rounded-full overflow-hidden"
          style={{ background: "var(--bg-raised)" }}
        >
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.min(v, 100)}%`, background: color }}
          />
        </div>
      </div>
    );
  }
  if (tab === "KDJ" && data.KDJ?.length) {
    const latest = data.KDJ.at(-1)!;
    return (
      <div className="flex gap-6 text-sm mono">
        <div>
          <span style={{ color: "var(--text-dim)" }}>K </span>
          <span style={{ color: "#facc15" }}>{latest.k.toFixed(2)}</span>
        </div>
        <div>
          <span style={{ color: "var(--text-dim)" }}>D </span>
          <span style={{ color: "#f97316" }}>{latest.d.toFixed(2)}</span>
        </div>
        <div>
          <span style={{ color: "var(--text-dim)" }}>J </span>
          <span style={{ color: "#a78bfa" }}>{latest.j.toFixed(2)}</span>
        </div>
      </div>
    );
  }
  if (tab === "BOLL" && data.BOLL?.length) {
    const latest = data.BOLL.at(-1)!;
    return (
      <div className="flex gap-6 text-sm mono">
        <div>
          <span style={{ color: "var(--text-dim)" }}>UP </span>
          <span style={{ color: "var(--up)" }}>
                      {latest.upper.toFixed(2)}
                    </span>
        </div>
        <div>
          <span style={{ color: "var(--text-dim)" }}>MID </span>
          <span style={{ color: "var(--accent)" }}>
            {latest.middle.toFixed(2)}
          </span>
        </div>
        <div>
          <span style={{ color: "var(--text-dim)" }}>LO </span>
          <span style={{ color: "var(--down)" }}>{latest.lower.toFixed(2)}</span>
        </div>
      </div>
    );
  }
  return (
    <p className="text-sm" style={{ color: "var(--text-dim)" }}>
      暂无数据
    </p>
  );
}

/* ------ Page ---------------------------------------------------------------------------------------------------------------------------------- */
export default function StockDetailPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = use(params);
  const [period, setPeriod] = useState<string>("daily");
  const [indTab, setIndTab] = useState<string>("MACD");

  const { data: quoteRaw, isLoading: loadingQ } = useSWR<Quote>(
    `/api/stock/${code}/quote`,
    fetcher,
    { refreshInterval: 30_000, revalidateOnFocus: false },
  );
  const quote = useMemo(() => {
    if (!quoteRaw || (quoteRaw as Quote & { error?: string }).error) return undefined;
    const q = quoteRaw as Quote & { price?: number; change_amount?: number };
    return {
      ...q,
      current: q.current ?? q.price,
      change: q.change ?? q.change_amount,
    };
  }, [quoteRaw]);

  const { data: indicatorsRaw, isLoading: loadingI, error: indError } = useSWR<
    IndicatorsData & { error?: string }
  >(
    `/api/stock/${code}/indicators?indicators=MA,MACD,RSI,KDJ,BOLL&period=${period}`,
    fetcher,
    { revalidateOnFocus: false },
  );
  const indicators =
    indicatorsRaw && !indicatorsRaw.error ? indicatorsRaw : undefined;
  const indicatorsError = indicatorsRaw?.error ?? (indError ? String(indError) : undefined);

  const { data: fundamentals } = useSWR<Fundamentals>(
    `/api/stock/${code}/fundamentals`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const { data: newsRaw } = useSWR<NewsItem[] | { news?: NewsItem[] }>(
    `/api/stock/${code}/news?days=7`,
    fetcher,
    { revalidateOnFocus: false },
  );
  const news: NewsItem[] = Array.isArray(newsRaw)
    ? newsRaw
    : ((newsRaw as { news?: NewsItem[] })?.news ?? []);

  const bars = useMemo(() => {
    if (!indicators?.bars) return [];
    return indicators.bars.map((b) => ({
      time: b.date,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));
  }, [indicators]);

  const maLines = useMemo(() => {
    if (!indicators?.MA || !indicators.MA_periods) return [];
    return indicators.MA.map((line, i) => ({
      label: `MA${indicators.MA_periods![i]}`,
      color: MA_COLORS[i % MA_COLORS.length],
      data: line
        .filter((p) => p.value != null)
        .map((p) => ({ time: p.date, value: p.value })),
    }));
  }, [indicators]);

  const name = quote?.name ?? fundamentals?.name ?? code;

  return (
    <div className="flex flex-col gap-6">
      {/* Back + title */}
      <div className="flex items-center gap-3">
        <Link
          href="/stock"
          className="flex items-center gap-1 text-sm no-underline"
          style={{ color: "var(--text-muted)" }}
        >
          <ArrowLeft size={14} /> 返回
        </Link>
        <span style={{ color: "var(--border-dim)" }}>|</span>
        <h1
          className="text-base font-semibold"
          style={{ color: "var(--text)" }}
        >
          {name}
          <span
            className="ml-2 mono text-sm"
            style={{ color: "var(--text-dim)" }}
          >
            {code}
          </span>
        </h1>
      </div>

      {/* Quote header */}
      {loadingQ ? (
        <Skeleton style={{ height: "80px" }} />
      ) : quote ? (
        <QuoteHeader quote={quote} code={code} />
      ) : (
        <p style={{ color: "var(--text-dim)" }}>行情数据不可用</p>
      )}

      {/* Chart period tabs */}
      <div className="flex gap-1">
        {PERIOD_TABS.map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className="px-3 py-1 text-xs rounded transition-colors"
            style={{
              background: period === p ? "var(--bg-raised)" : "transparent",
              color: period === p ? "var(--text)" : "var(--text-muted)",
              border: `1px solid ${period === p ? "var(--border-dim)" : "transparent"}`,
              cursor: "pointer",
            }}
          >
            {PERIOD_LABELS[p as keyof typeof PERIOD_LABELS]}
          </button>
        ))}
      </div>

      {/* Candlestick chart */}
      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: "12px",
          overflow: "hidden",
        }}
      >
        {loadingI ? (
          <Skeleton style={{ height: "320px" }} />
        ) : bars.length > 0 ? (
          <StockChart bars={bars} maLines={maLines} height={320} />
        ) : (
          <div
            style={{
              height: "320px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <p style={{ color: "var(--text-dim)" }}>
              {indicatorsError ? `暂无K线数据：${indicatorsError}` : "暂无K线数据"}
            </p>
          </div>
        )}
      </div>

      {/* Indicator tabs */}
      <div
        style={{
          background: "var(--bg-panel)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          overflow: "hidden",
        }}
      >
        <div
          className="flex"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          {INDICATOR_TABS.map((t) => (
            <button
              key={t}
              onClick={() => setIndTab(t)}
              className="px-4 py-2 text-xs mono transition-colors"
              style={{
                background: "transparent",
                color: indTab === t ? "var(--accent)" : "var(--text-muted)",
                borderTop: "none",
                borderLeft: "none",
                borderRight: "none",
                borderBottomWidth: "2px",
                borderBottomStyle: "solid",
                borderBottomColor:
                  indTab === t ? "var(--accent)" : "transparent",
                cursor: "pointer",
              }}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="p-4">
          {loadingI ? (
            <Skeleton style={{ height: "80px" }} />
          ) : indicators ? (
            <IndicatorChart data={indicators} tab={indTab} />
          ) : (
            <p style={{ color: "var(--text-dim)" }}>暂无指标数据</p>
          )}
        </div>
      </div>

      {/* Fundamentals */}
      {fundamentals && (
        <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatTile
            label="市盈率 (PE)"
            value={fundamentals.pe?.toFixed(2) ?? "-"}
          />
          <StatTile
            label="市净率 (PB)"
            value={fundamentals.pb?.toFixed(2) ?? "-"}
          />
          <StatTile
            label="ROE"
            value={
              fundamentals.roe != null ? `${fundamentals.roe.toFixed(2)}%` : "-"
            }
          />
          <StatTile
            label="营收增速 (YoY)"
            value={
              fundamentals.revenue_yoy != null
                ? fmtPct(fundamentals.revenue_yoy)
                : "-"
            }
            valueColor={
              fundamentals.revenue_yoy != null
                ? fundamentals.revenue_yoy >= 0
                  ? "var(--up)"
                  : "var(--down)"
                : undefined
            }
          />
        </section>
      )}

      {/* News */}
      {news.length > 0 && (
        <section>
          <h2
            className="text-sm font-medium mb-3 flex items-center gap-1.5"
            style={{ color: "var(--text-muted)" }}
          >
            <Newspaper size={14} /> 相关新闻
          </h2>
          <div
            className="flex flex-col"
            style={{
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
            }}
          >
            {news.slice(0, 10).map((n, i) => (
              <div
                key={n.id ?? i}
                className="px-4 py-3"
                style={{
                  borderBottom:
                    i < news.length - 1 ? "1px solid var(--border)" : "none",
                }}
              >
                <p className="text-sm" style={{ color: "var(--text)" }}>
                  {n.title}
                </p>
                {n.publish_time && (
                  <p
                    className="text-xs mono mt-1"
                    style={{ color: "var(--text-dim)" }}
                  >
                    {n.publish_time}
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
