"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  type IChartApi,
  type CandlestickData,
  type LineData,
  ColorType,
} from "lightweight-charts";

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

export function StockChart({ bars, maLines = [], height = 320 }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);

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
      timeScale: { borderColor: "#27272a", timeVisible: true },
      height,
      autoSize: true,
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor:         "#22c55e",
      downColor:       "#ef4444",
      borderUpColor:   "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor:     "#22c55e",
      wickDownColor:   "#ef4444",
    });

    candles.setData(bars as CandlestickData[]);

    for (const ma of maLines) {
      const series = chart.addSeries(LineSeries, {
        color:       ma.color,
        lineWidth:   1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      series.setData(ma.data);
    }

    chart.timeScale().fitContent();
    chartRef.current = chart;

    return () => { chart.remove(); chartRef.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, maLines, height]);

  return <div ref={containerRef} style={{ width: "100%", height }} />;
}
