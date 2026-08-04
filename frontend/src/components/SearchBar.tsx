"use client";

import useSWR from "swr";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import { fetcher } from "@/lib/api";
import { useDebouncedValue } from "@/lib/use-debounced";

export function SearchBar() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const debouncedQ = useDebouncedValue(q.trim(), 250);
  const { data: results } = useSWR<{ matches?: { code: string; name: string }[] }>(
    debouncedQ.length >= 1 ? `/api/stock/resolve?q=${encodeURIComponent(debouncedQ)}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const items = results?.matches ?? [];

  function handleSelect(code: string) {
    setQ("");
    setOpen(false);
    router.push(`/stock/${code}`);
  }

  return (
    <div className="relative w-full max-w-sm">
      <div
        className="flex items-center gap-2 px-3 rounded"
        style={{
          background: "color-mix(in srgb, var(--bg-elevated) 84%, transparent)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          height: "40px",
        }}
      >
        <MagnifyingGlass size={15} style={{ color: "var(--text-dim)", flexShrink: 0 }} />
        <input
          className="flex-1 bg-transparent outline-none text-sm"
          style={{ color: "var(--text)" }}
          placeholder="搜索股票..."
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && items.length > 0) handleSelect(items[0].code);
          }}
        />
      </div>
      {open && items.length > 0 && (
        <ul
          className="absolute left-0 right-0 mt-1 py-1 z-50"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            boxShadow: "var(--shadow-soft)",
            maxHeight: "220px",
            overflowY: "auto",
          }}
        >
          {items.slice(0, 8).map((r) => (
            <li
              key={r.code}
              className="flex items-center justify-between px-3 py-2 cursor-pointer text-sm hover:bg-[var(--bg-raised)] transition-colors"
              onMouseDown={() => handleSelect(r.code)}
            >
              <span style={{ color: "var(--text)" }}>{r.name}</span>
              <span className="mono text-xs" style={{ color: "var(--text-dim)" }}>
                {r.code}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
