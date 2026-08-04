import { fmtPct } from "@/lib/format";

interface PriceChangeProps {
  value: number | null | undefined;
  showSign?: boolean;
  className?: string;
}

export function PriceChange({ value, showSign = true, className = "" }: PriceChangeProps) {
  const isUp   = typeof value === "number" && value > 0;
  const isDown = typeof value === "number" && value < 0;
  const color  = isUp ? "var(--up)" : isDown ? "var(--down)" : "var(--text-muted)";
  const text   = showSign ? fmtPct(value) : (value != null ? `${Math.abs(value).toFixed(2)}%` : "-");

  return (
    <span className={`mono ${className}`} style={{ color }}>
      {text}
    </span>
  );
}
