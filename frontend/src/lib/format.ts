/** Format a price number with 2 decimal places */
export function fmtPrice(n: number | null | undefined, prefix = ""): string {
  if (n == null || isNaN(n)) return "-";
  return `${prefix}${n.toFixed(2)}`;
}

/** Format a percent change: +2.34% / -1.20% */
export function fmtPct(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "-";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

/** Format a large number with 亿/万 unit */
export function fmtVol(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "-";
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return n.toFixed(0);
}

/** Format amount (yuan) */
export function fmtAmount(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return "-";
  if (Math.abs(n) >= 1e8) return `¥${(n / 1e8).toFixed(2)}亿`;
  if (Math.abs(n) >= 1e4) return `¥${(n / 1e4).toFixed(2)}万`;
  return `¥${n.toFixed(2)}`;
}

/** Returns "up" | "down" | "flat" for coloring */
export function direction(n: number | null | undefined): "up" | "down" | "flat" {
  if (n == null || isNaN(n)) return "flat";
  return n > 0 ? "up" : n < 0 ? "down" : "flat";
}

/** Color class for a change value */
export function changeColor(n: number | null | undefined): string {
  const d = direction(n);
  if (d === "up") return "text-up";
  if (d === "down") return "text-down";
  return "text-[var(--text-muted)]";
}
