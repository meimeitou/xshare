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

/** Format a 'YYYY-MM-DD' date string (or BusinessDay) for axis/tooltip display.
 *  - axisShort: 'MM-DD'        (used for daily tick labels)
 *  - axisYear:  'YYYY-MM'      (used at year-boundary ticks)
 *  - full:      'YYYY-MM-DD'   (used in the tooltip header)
 *  Pass-through for already-formatted strings or non-ISO inputs: return as-is. */
export function fmtDate(
  time: { year: number; month: number; day: number } | string | number,
  mode: "axisShort" | "axisYear" | "full",
): string {
  let y: string, m: string, d: string;
  if (typeof time === "string") {
    const s = time.slice(0, 10);              // tolerate trailing time/extra chars
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
      [y, m, d] = [s.slice(0, 4), s.slice(5, 7), s.slice(8, 10)];
    } else {
      return s;                                // not ISO - show raw
    }
  } else if (typeof time === "number") {
    // UTCTimestamp (epoch seconds) - not a business day we can parse without a date lib
    return String(time);
  } else {
    y = String(time.year);
    m = String(time.month).padStart(2, "0");
    d = String(time.day).padStart(2, "0");
  }
  if (mode === "axisShort") return `${m}-${d}`;
  if (mode === "axisYear")  return `${y}-${m}`;
  return `${y}-${m}-${d}`;
}
