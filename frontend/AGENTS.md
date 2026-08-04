<\!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<\!-- END:nextjs-agent-rules -->

# XShare Frontend

Next.js 16 (App Router) dashboard for the XShare financial data MCP server.

## Stack

- **Framework**: Next.js 16 App Router, TypeScript, `src/` directory
- **Styling**: Tailwind v4 (`@import "tailwindcss"` in globals.css, no tailwind.config.ts)
- **Fonts**: `geist` npm package (`GeistSans`/`GeistMono` from `geist/font/sans|mono`) — NOT `next/font/google`
- **Animation**: `motion/react` (import from `motion/react`, not `framer-motion`)
- **Data fetching**: `swr` with `fetcher` from `@/lib/api`
- **Icons**: `@phosphor-icons/react` only — do NOT use lucide-react or hand-roll SVGs
- **Charts**: `lightweight-charts` (TradingView) for financial candlestick/line charts

## Design tokens (globals.css CSS vars)

```
--bg         #09090b  zinc-950 background
--bg-panel   #18181b  zinc-900 cards/panels
--bg-raised  #1f1f23  hover states
--border     #27272a  zinc-800 dividers
--text        #fafafa  primary text
--text-muted  #a1a1aa  secondary text
--text-dim    #71717a  tertiary / labels
--accent      #00d8d6  electric cyan — the one accent color
--up          #22c55e  price up / positive
--down        #ef4444  price down / negative
--radius      6px      corner radius (consistent everywhere)
```

Use CSS variables directly (`style={{ color: "var(--accent)" }}`), not Tailwind color utilities, for these tokens.

## API

Backend runs at `http://127.0.0.1:8080` (env: `NEXT_PUBLIC_API_URL`).
Use `fetcher` from `@/lib/api` as the SWR fetcher, `apiFetch` for mutations.

Key endpoints: `/api/market/overview`, `/api/market/mainline`, `/api/stock/resolve?q=`,
`/api/stock/{code}/quote`, `/api/stock/{code}/indicators`, `/api/stock/{code}/fundamentals`,
`/api/stock/{code}/news`, `/api/portfolio`, `/api/sync/jobs`, `/api/sync/coverage`, `/api/sync/history`, `/api/sync/tasks/{id}`.

## Pages

| Route | File |
|-------|------|
| `/` | `src/app/page.tsx` — market overview |
| `/stock` | `src/app/stock/page.tsx` — search landing |
| `/stock/[code]` | `src/app/stock/[code]/page.tsx` — detail: quote, chart, indicators, news |
| `/portfolio` | `src/app/portfolio/page.tsx` — positions table + add/delete |
| `/sync` | `src/app/sync/page.tsx` — sync jobs table + queue table; history in right drawer |

## Rules

- All data-fetching pages are `"use client"` + SWR. No `fetch` in Server Components (data is too dynamic).
- `StockChart` (`src/components/StockChart.tsx`) is a client-only leaf component — never import it from a Server Component without `dynamic`.
- NEVER add `window.addEventListener("scroll", ...)` — use Motion `useScroll()` or IntersectionObserver.
- One accent color: `--accent`. Red/green (`--up`/`--down`) are semantic price colors only.
- Numbers displayed in data tables/quotes use `font-family: var(--font-geist-mono)` (add class `mono`).
- ZERO em-dashes anywhere. Use hyphens or restructure the sentence.
- Tailwind v4 gotcha: do NOT add `tailwindcss` plugin to `postcss.config.js` — the project uses `@tailwindcss/postcss`.
- Turbopack gotcha: box-drawing Unicode chars (─ ┌ └) in source files cause a Rust panic in the build. Use plain hyphens in comments.
