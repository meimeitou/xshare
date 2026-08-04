"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Asset = { code: string; name: string; asset_type: string; asset_type_label: string; market?: string; exchange?: string; industry?: string; index_name?: string; category?: string; list_date?: string; data_count: number; latest_trade_date?: string; first_trade_date?: string; has_one_year_data?: boolean; sync_status?: string | null; last_sync_at?: string | null };
type Response = { items: Asset[]; total: number };

function daysSince(dateStr?: string): string {
  if (!dateStr) return "-";
  const d = new Date(dateStr.length === 8 ? `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}` : dateStr);
  if (isNaN(d.getTime())) return "-";
  const diff = Math.floor((Date.now() - d.getTime()) / 86400000);
  return diff < 0 ? "-" : String(diff);
}

function fmtSyncTime(ts?: string | null): string {
  if (!ts) return "-";
  // 后端返回 "YYYY-MM-DD HH:MM:SS.ffffff"，截取到分钟
  return ts.slice(0, 16);
}

function syncBadge(a: Asset) {
  const st = a.sync_status;
  if (st === "ok") {
    return <span className="mono" style={{ color: "var(--up)" }}>成功</span>;
  }
  if (st === "partial" || st === "pending") {
    return <span className="mono" style={{ color: "var(--text-dim)" }}>{st === "partial" ? "部分" : "待同步"}</span>;
  }
  if (st === "error") {
    return <span className="mono" style={{ color: "var(--down)" }}>失败</span>;
  }
  return <span className="mono" style={{ color: "var(--text-dim)" }}>-</span>;
}

export default function StockIndexPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [type, setType] = useState("");
  const [syncStatus, setSyncStatus] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  useEffect(() => { const t = setTimeout(() => { setDebouncedQ(q); setPage(1); }, 250); return () => clearTimeout(t); }, [q]);
  const params = new URLSearchParams({ limit: String(pageSize), offset: String((page - 1) * pageSize) });
  if (debouncedQ.trim()) params.set("q", debouncedQ.trim());
  if (type) params.set("type", type);
  if (syncStatus) params.set("sync_status", syncStatus);
  const { data, error, isLoading } = useSWR<Response>(
    `/api/stock/list?${params}`,
    (url: string) => fetcher(url) as Promise<Response>,
    { revalidateOnFocus: false },
  );
  const items = data?.items ?? [];
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));
  const pageItems: (number | "ellipsis")[] = [];
  for (let current = 1; current <= pageCount; current++) {
    if (current === 1 || current === pageCount || Math.abs(current - page) <= 2) {
      pageItems.push(current);
    } else if (pageItems[pageItems.length - 1] !== "ellipsis") {
      pageItems.push("ellipsis");
    }
  }
  const label = (a: Asset) => a.asset_type === "etf" ? a.index_name || a.exchange || "-" : a.asset_type === "index" ? a.category || a.market || "-" : a.industry || a.market || "-";
  return <main className="mx-auto w-full max-w-6xl px-6 py-8">
    <div className="flex flex-wrap items-end justify-between gap-4 mb-6"><div><h1 className="text-xl font-semibold" style={{color:"var(--text)"}}>资产列表</h1><p className="text-sm mt-1" style={{color:"var(--text-dim)"}}>{data?.total ?? "-"} 项资产</p></div>
      <div className="flex items-center gap-2 px-3 w-full max-w-sm" style={{background:"var(--bg-panel)", border:"1px solid var(--border)", borderRadius:"var(--radius)", height:40}}><MagnifyingGlass size={16} style={{color:"var(--text-dim)"}}/><input className="flex-1 bg-transparent outline-none text-sm" style={{color:"var(--text)"}} placeholder="搜索代码或名称" value={q} onChange={e=>setQ(e.target.value)} /></div></div>
    <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1">{[["","全部"],["stock","股票"],["etf","ETF"],["index","指数"]].map(([v,l])=><button key={v} onClick={()=>{setType(v);setPage(1)}} className="px-3 py-1.5 text-sm" style={{color:type===v?"var(--text)":"var(--text-dim)",background:type===v?"var(--bg-raised)":"transparent",border:"1px solid var(--border)",borderRadius:"var(--radius)"}}>{l}</button>)}</div>
        <select value={syncStatus} onChange={e=>{setSyncStatus(e.target.value);setPage(1)}} className="px-2 py-1.5 text-sm outline-none" style={{background:"var(--bg-panel)",color:syncStatus?"var(--text)":"var(--text-dim)",border:"1px solid var(--border)",borderRadius:"var(--radius)"}} aria-label="同步状态筛选">
          {[["","同步状态: 全部"],["ok","同步成功"],["unsynced","未同步"],["error","同步失败"],["partial","部分"],["pending","待同步"]].map(([v,l])=><option key={v||"all"} value={v}>{l}</option>)}
        </select>
      </div>
      <label className="flex items-center gap-2 text-sm" style={{color:"var(--text-dim)"}}>每页
        <select value={pageSize} onChange={e=>{setPageSize(Number(e.target.value));setPage(1)}} className="px-2 py-1.5 outline-none" style={{background:"var(--bg-panel)",color:"var(--text)",border:"1px solid var(--border)",borderRadius:"var(--radius)"}}>
          {[20, 50, 100].map(size=><option key={size} value={size}>{size} 行</option>)}
        </select>
      </label>
    </div>
    <div className="overflow-x-auto" style={{border:"1px solid var(--border)", borderRadius:"var(--radius)"}}><table className="w-full text-sm"><thead><tr style={{color:"var(--text-dim)", borderBottom:"1px solid var(--border)"}}>{["类型","名称","代码","市场/交易所","行业/跟踪指数/类别","上市日期","上市天数","数据量","同步状态","上次同步时间","最新行情"].map(h=><th key={h} className="text-left px-4 py-3 font-medium">{h}</th>)}</tr></thead><tbody>{isLoading ? <tr><td colSpan={11} className="px-4 py-10 text-center" style={{color:"var(--text-dim)"}}>加载中...</td></tr> : error ? <tr><td colSpan={11} className="px-4 py-10 text-center" style={{color:"var(--text-dim)"}}>请求失败，请稍后重试</td></tr> : items.length===0 ? <tr><td colSpan={11} className="px-4 py-10 text-center" style={{color:"var(--text-dim)"}}>暂无匹配资产</td></tr> : items.map(a=><tr key={a.code} tabIndex={0} onClick={()=>router.push(`/stock/${a.code}`)} onKeyDown={e=>{if(e.key==="Enter")router.push(`/stock/${a.code}`)}} className="cursor-pointer hover:bg-[var(--bg-raised)]" style={{borderBottom:"1px solid var(--border)"}}><td className="px-4 py-3">{a.asset_type_label}</td><td className="px-4 py-3" style={{color:"var(--text)"}}>{a.name}</td><td className="px-4 py-3 mono">{a.code}</td><td className="px-4 py-3">{a.market||a.exchange||"-"}</td><td className="px-4 py-3">{label(a)}</td><td className="px-4 py-3 mono">{a.list_date||"-"}</td><td className="px-4 py-3 mono">{daysSince(a.list_date)}</td><td className="px-4 py-3 mono">{a.data_count}</td><td className="px-4 py-3">{syncBadge(a)}</td><td className="px-4 py-3 mono" style={{color:"var(--text-dim)"}}>{fmtSyncTime(a.last_sync_at)}</td><td className="px-4 py-3 mono">{a.latest_trade_date||"-"}</td></tr>)}</tbody></table></div>
    {data && pageCount > 1 && <nav aria-label="资产列表分页" className="flex flex-wrap items-center justify-end gap-1 mt-4">
      <button disabled={page===1} onClick={()=>setPage(page-1)} className="px-3 py-1.5 text-sm disabled:opacity-40" style={{border:"1px solid var(--border)",borderRadius:"var(--radius)",color:"var(--text)"}}>上一页</button>
      {pageItems.map((item,index)=>item === "ellipsis" ? <span key={`ellipsis-${index}`} className="px-2" style={{color:"var(--text-dim)"}}>...</span> : <button key={item} aria-current={item===page ? "page" : undefined} onClick={()=>setPage(item)} className="min-w-9 px-2 py-1.5 text-sm" style={{border:"1px solid var(--border)",borderRadius:"var(--radius)",color:"var(--text)",background:item===page?"var(--bg-raised)":"transparent"}}>{item}</button>)}
      <button disabled={page===pageCount} onClick={()=>setPage(page+1)} className="px-3 py-1.5 text-sm disabled:opacity-40" style={{border:"1px solid var(--border)",borderRadius:"var(--radius)",color:"var(--text)"}}>下一页</button>
    </nav>}
  </main>;
}
