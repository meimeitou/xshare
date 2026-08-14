"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  ColorType,
  TickMarkType,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type LineData,
  type BarData,
  type Time,
  type MouseEventParams,
} from "lightweight-charts";
import { fmtDate, fmtPrice } from "@/lib/format";

export interface OHLCBar {
  time: string; // "YYYY-MM-DD"
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface MALine {
  label: string;
  color: string;
  data: LineData[];
}

interface StockChartProps {
  bars: OHLCBar[];
  maLines?: MALine[];
  height?: number;
}

interface TooltipData {
  date: string;            // 'YYYY-MM-DD'
  open: number; high: number; low: number; close: number;
  prevClose: number | null; // previous bar's close, for OHLC coloring
  ma: { label: string; color: string; value: number }[];
}

/** A-share convention: red = up, green = down. Colors a value relative to prevClose. */
function ohlcColor(val: number, prevClose: number | null): string {
  if (prevClose == null) return "var(--text-muted)";
  if (val > prevClose) return "var(--up)";
  if (val < prevClose) return "var(--down)";
  return "var(--text-muted)";
}

export function StockChart({ bars, maLines = [], height = 320 }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const candlesRef   = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const maSeriesRef  = useRef<{ series: ISeriesApi<"Line">; label: string; color: string }[]>([]);
  const [tip, setTip] = useState<TooltipData | null>(null);

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#a1a1aa",
        fontFamily: "var(--font-geist-mono), monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#27272a" },
        horzLines: { color: "#27272a" },
      },
      crosshair: {
        vertLine: { color: "#52525b", labelBackgroundColor: "#18181b" },
        horzLine: { color: "#52525b", labelBackgroundColor: "#18181b" },
      },
      rightPriceScale: { borderColor: "#27272a" },
      timeScale: {
        borderColor: "#27272a",
        timeVisible: false,
        tickMarkFormatter: (time: Time, tickType: TickMarkType) => {
          if (tickType === TickMarkType.Year) return fmtDate(time, "axisYear"); // 'YYYY-MM'
          return fmtDate(time, "axisShort");                                   // 'MM-DD'
        },
      },
      height,
      autoSize: true,
    });

    // A-share convention: red = up, green = down (via CSS vars --up / --down)
    const cssVars = getComputedStyle(containerRef.current);
    const upHex   = cssVars.getPropertyValue("--up").trim()   || "#d64545";
    const downHex = cssVars.getPropertyValue("--down").trim() || "#0f9f62";
    
    const candles = chart.addSeries(CandlestickSeries, {
      upColor:         upHex,
      downColor:       downHex,
      borderUpColor:   upHex,
      borderDownColor: downHex,
      wickUpColor:     upHex,
      wickDownColor:   downHex,
    });
    candles.setData(bars as CandlestickData[]);
    candlesRef.current = candles;

    maSeriesRef.current = [];
    for (const ma of maLines) {
      const series = chart.addSeries(LineSeries, {
        color:       ma.color,
        lineWidth:   1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      series.setData(ma.data);
      maSeriesRef.current.push({ series, label: ma.label, color: ma.color });
    }

    chart.timeScale().fitContent();

    const onMove = (param: MouseEventParams) => {
      if (!param.point || !param.time || !candlesRef.current) {
        setTip(null);
        return;
      }
      const cd = param.seriesData.get(candlesRef.current) as
        | (BarData & { open: number; high: number; low: number; close: number })
        | undefined;
      if (!cd) { setTip(null); return; }
      const dateStr = fmtDate(param.time, "full");
      const idx = bars.findIndex((b) => b.time === dateStr);
      const prevClose = idx > 0 ? bars[idx - 1].close : null;
      const ma = maSeriesRef.current
        .map(({ series, label, color }) => {
          const d = param.seriesData.get(series) as LineData | undefined;
          return d ? { label, color, value: d.value } : null;
        })
        .filter((x): x is { label: string; color: string; value: number } => x !== null);
      setTip({ date: dateStr, open: cd.open, high: cd.high, low: cd.low, close: cd.close, prevClose, ma });
    };
    chart.subscribeCrosshairMove(onMove);

    chartRef.current = chart;

    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      candlesRef.current = null;
      maSeriesRef.current = [];
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, maLines, height]);

  return (
    <div style={{ position: "relative", width: "100%", height }}>
      <div ref={containerRef} style={{ width: "100%", height }} />
      {tip && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            zIndex: 10,
            padding: "6px 8px",
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            fontFamily: "var(--font-geist-mono), monospace",
            fontSize: 11,
            lineHeight: 1.5,
            color: "var(--text)",
            pointerEvents: "none",
            whiteSpace: "nowrap",
          }}
        >
          <div style={{ color: "var(--text-muted)", marginBottom: 2 }}>{tip.date}</div>
          <div>
            开 <span style={{ color: ohlcColor(tip.open, tip.prevClose) }}>{fmtPrice(tip.open)}</span>
            高 <span style={{ color: ohlcColor(tip.high, tip.prevClose) }}>{fmtPrice(tip.high)}</span>
          </div>
          <div>
            低 <span style={{ color: ohlcColor(tip.low, tip.prevClose) }}>{fmtPrice(tip.low)}</span>
            收 <span style={{ color: ohlcColor(tip.close, tip.prevClose) }}>{fmtPrice(tip.close)}</span>
          </div>
          {tip.ma.length > 0 && (
            <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
              {tip.ma.map((m) => (
                <span key={m.label} style={{ color: m.color }}>
                  {m.label} {fmtPrice(m.value)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
