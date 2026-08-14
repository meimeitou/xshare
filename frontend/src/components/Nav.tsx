"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChartLineUp,
  ArrowsClockwise,
  Briefcase,
  Globe,
  ChatCircleDots,
} from "@phosphor-icons/react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

const links = [
  { href: "/", label: "市场", Icon: Globe },
  { href: "/stock", label: "股票", Icon: ChartLineUp },
  { href: "/ask", label: "问股", Icon: ChatCircleDots },
  { href: "/portfolio", label: "持仓", Icon: Briefcase },
  { href: "/sync", label: "同步", Icon: ArrowsClockwise },
];

export function Nav() {
  const path = usePathname();
  const { data } = useSWR("/api/health", fetcher, {
    refreshInterval: 15_000,
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
  const healthy = !!(data as { status?: string } | undefined)?.status;

  return (
    <header
      className="sticky top-0 z-40 w-full"
      style={{
        background: "color-mix(in srgb, var(--bg) 86%, transparent)",
        backdropFilter: "blur(14px)",
        borderBottom: "1px solid var(--border)",
        height: "68px",
      }}
    >
      <div
        className="max-w-[1400px] mx-auto px-4 md:px-6 flex items-center justify-between"
        style={{ height: "68px" }}
      >
        {/* Wordmark */}
        <Link href="/" className="flex items-center gap-2 no-underline">
          <span
            className="mono text-sm font-bold tracking-tight"
            style={{ color: "var(--accent-strong)" }}
          >
            XShare
          </span>
          <span
            className="text-[11px] mono px-1.5 py-0.5"
            style={{
              borderRadius: "999px",
              background: "var(--accent-soft)",
              color: "var(--accent-strong)",
              border:
                "1px solid color-mix(in srgb, var(--accent) 38%, transparent)",
            }}
          >
            A股
          </span>
        </Link>

        {/* Nav links */}
        <nav className="hidden md:flex items-center gap-1">
          {links.map(({ href, label, Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors"
                style={{
                  borderRadius: "10px",
                  color: active ? "var(--text)" : "var(--text-muted)",
                  background: active
                    ? "color-mix(in srgb, var(--bg-elevated) 90%, transparent)"
                    : "transparent",
                  border: active
                    ? "1px solid color-mix(in srgb, var(--accent) 22%, var(--border))"
                    : "1px solid transparent",
                }}
              >
                <Icon size={15} weight={active ? "fill" : "regular"} />
                {label}
              </Link>
            );
          })}
        </nav>

        <nav className="md:hidden flex items-center gap-1">
          {links.map(({ href, label, Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-label={label}
                className="w-9 h-9 flex items-center justify-center"
                style={{
                  borderRadius: "10px",
                  color: active ? "var(--text)" : "var(--text-muted)",
                  background: active ? "var(--bg-strong)" : "transparent",
                  border: active
                    ? "1px solid var(--border)"
                    : "1px solid transparent",
                }}
              >
                <Icon size={16} weight={active ? "fill" : "regular"} />
              </Link>
            );
          })}
        </nav>

        {/* API health indicator */}
        <div
          className="hidden sm:flex items-center gap-1.5 px-2.5 py-1.5"
          style={{
            border: "1px solid var(--border)",
            borderRadius: "999px",
            background: "color-mix(in srgb, var(--bg-panel) 75%, transparent)",
          }}
        >
          <span
            className="w-2 h-2 rounded-full"
            style={{ background: healthy ? "var(--success)" : "var(--text-dim)" }}
          />
          <span className="text-xs mono" style={{ color: "var(--text-dim)" }}>
            {healthy ? "API ONLINE" : "API OFFLINE"}
          </span>
        </div>
      </div>
    </header>
  );
}
