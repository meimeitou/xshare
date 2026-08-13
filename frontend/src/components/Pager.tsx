"use client";

/** Shared pagination control — page buttons 1..total */
export function Pager({
  page,
  total,
  onPage,
}: {
  page: number;
  total: number;
  onPage: (p: number) => void;
}) {
  if (total <= 1) return null;
  return (
    <div className="flex items-center justify-center gap-1.5 mt-2">
      {[...Array(total)].map((_, i) => (
        <button
          key={i}
          onClick={() => onPage(i)}
          className="px-2 py-0.5 text-xs rounded transition-colors"
          style={{
            background: i === page ? "var(--accent)" : "transparent",
            color: i === page ? "var(--bg)" : "var(--text-dim)",
            border: "1px solid var(--border)",
          }}
        >
          {i + 1}
        </button>
      ))}
    </div>
  );
}
