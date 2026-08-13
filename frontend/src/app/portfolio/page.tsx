"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher, apiFetch } from "@/lib/api";
import { fmtAmount, changeColor, fmtPct } from "@/lib/format";
import { StatTile } from "@/components/StatTile";
import { Skeleton } from "@/components/Skeleton";
import { Plus, Trash, X, MagnifyingGlass } from "@phosphor-icons/react";

/* ------ Types -------------------------------------------------------------------------------------------------------------------------------- */
interface PortfolioRecord {
  id: number;
  code: string;
  name?: string;
  direction: "buy" | "sell";
  trade_date: string;
  price: number;
  quantity: number;
  amount: number;
  pnl?: number;
  pnl_pct?: number;
  memo?: string;
}
interface PortfolioSummary {
  records?: PortfolioRecord[];
  total_cost?: number;
  total_value?: number;
  total_pnl?: number;
  realized_pnl?: number;
  positions?: number;
}

/* ------ Add Trade Form (slide-in panel) -------------------------------------------------------------------------- */
function AddTradeForm({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [form, setForm] = useState({
    action: "buy",
    code: "",
    price: "",
    quantity: "",
    trade_date: new Date().toISOString().slice(0, 10),
    memo: "",
  });
  const [q, setQ] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const { data: searchResults } = useSWR<{
    matches?: { code: string; name: string }[];
  }>(
    q.trim().length >= 1
      ? `/api/stock/resolve?q=${encodeURIComponent(q)}`
      : null,
    fetcher,
    { revalidateOnFocus: false },
  );
  const items = searchResults?.matches ?? [];

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!form.code || !form.price || !form.quantity) {
      setError("请填写必填字段");
      return;
    }
    setSaving(true);
    try {
      await apiFetch("/api/portfolio", {
        method: "POST",
        body: JSON.stringify({
          action: form.action,
          code: form.code,
          price: parseFloat(form.price),
          quantity: parseInt(form.quantity),
          trade_date: form.trade_date || undefined,
          memo: form.memo || undefined,
        }),
      });
      onSuccess();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const labelStyle = {
    color: "var(--text-muted)",
    fontSize: "12px",
    marginBottom: "4px",
    display: "block",
  };
  const inputStyle = {
    background: "var(--bg-raised)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    color: "var(--text)",
    padding: "8px 10px",
    fontSize: "13px",
    width: "100%",
    outline: "none",
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="flex flex-col w-full max-w-sm h-full"
        style={{
          background: "var(--bg-panel)",
          borderLeft: "1px solid var(--border)",
        }}
      >
        <div
          className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <h2
            className="text-sm font-semibold"
            style={{ color: "var(--text)" }}
          >
            添加交易记录
          </h2>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
            }}
          >
            <X size={18} />
          </button>
        </div>
        <form
          onSubmit={handleSubmit}
          className="flex-1 overflow-y-auto p-5 flex flex-col gap-4"
        >
          {/* Direction */}
          <div>
            <label style={labelStyle}>方向</label>
            <div className="flex gap-2">
              {(["buy", "sell"] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, action: d }))}
                  className="flex-1 py-2 rounded text-sm transition-colors"
                  style={{
                    background:
                      form.action === d
                        ? d === "buy"
                          ? "color-mix(in srgb,var(--up) 15%,var(--bg-raised))"
                          : "color-mix(in srgb,var(--down) 15%,var(--bg-raised))"
                        : "var(--bg-raised)",
                    border: `1px solid ${form.action === d ? (d === "buy" ? "var(--up)" : "var(--down)") : "var(--border)"}`,
                    color:
                      form.action === d
                        ? d === "buy"
                          ? "var(--up)"
                          : "var(--down)"
                        : "var(--text-muted)",
                    cursor: "pointer",
                  }}
                >
                  {d === "buy" ? "买入" : "卖出"}
                </button>
              ))}
            </div>
          </div>

          {/* Stock search */}
          <div>
            <label style={labelStyle}>股票 *</label>
            <div className="relative">
              <div
                className="flex items-center gap-2"
                style={{ ...inputStyle, padding: "0 10px" }}
              >
                <MagnifyingGlass
                  size={13}
                  style={{ color: "var(--text-dim)", flexShrink: 0 }}
                />
                <input
                  className="flex-1 bg-transparent outline-none text-[13px]"
                  style={{ color: "var(--text)", height: "34px" }}
                  placeholder={form.code ? form.code : "输入名称或代码..."}
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value);
                    setSearchOpen(true);
                  }}
                  onFocus={() => setSearchOpen(true)}
                  onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
                />
              </div>
              {searchOpen && items.length > 0 && (
                <ul
                  className="absolute left-0 right-0 mt-1 py-1 z-50"
                  style={{
                    background: "var(--bg-panel)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
                    maxHeight: "200px",
                    overflowY: "auto",
                  }}
                >
                  {items.slice(0, 8).map((r) => (
                    <li
                      key={r.code}
                      className="flex items-center justify-between px-3 py-2 cursor-pointer text-xs hover:bg-[var(--bg-raised)] transition-colors"
                      onMouseDown={() => {
                        setForm((f) => ({ ...f, code: r.code }));
                        setQ(r.name);
                        setSearchOpen(false);
                      }}
                    >
                      <span style={{ color: "var(--text)" }}>{r.name}</span>
                      <span
                        className="mono"
                        style={{ color: "var(--text-dim)" }}
                      >
                        {r.code}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* Price */}
          <div>
            <label style={labelStyle}>成交价格 *</label>
            <input
              style={inputStyle}
              type="number"
              step="0.01"
              min="0"
              placeholder="0.00"
              value={form.price}
              onChange={(e) =>
                setForm((f) => ({ ...f, price: e.target.value }))
              }
              required
            />
          </div>

          {/* Quantity */}
          <div>
            <label style={labelStyle}>数量 (股) *</label>
            <input
              style={inputStyle}
              type="number"
              step="100"
              min="100"
              placeholder="100"
              value={form.quantity}
              onChange={(e) =>
                setForm((f) => ({ ...f, quantity: e.target.value }))
              }
              required
            />
          </div>

          {/* Trade date */}
          <div>
            <label style={labelStyle}>交易日期</label>
            <input
              style={inputStyle}
              type="date"
              value={form.trade_date}
              onChange={(e) =>
                setForm((f) => ({ ...f, trade_date: e.target.value }))
              }
            />
          </div>

          {/* Memo */}
          <div>
            <label style={labelStyle}>备注</label>
            <input
              style={inputStyle}
              type="text"
              placeholder="可选"
              value={form.memo}
              onChange={(e) => setForm((f) => ({ ...f, memo: e.target.value }))}
            />
          </div>

          {error && (
            <p className="text-xs" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={saving}
            className="w-full py-2.5 rounded text-sm font-medium transition-opacity"
            style={{
              background: "var(--accent)",
              color: "#09090b",
              cursor: saving ? "not-allowed" : "pointer",
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? "保存中..." : "确认添加"}
          </button>
        </form>
      </div>
    </div>
  );
}

/* ------ Page ---------------------------------------------------------------------------------------------------------------------------------- */
export default function PortfolioPage() {
  const [showForm, setShowForm] = useState(false);

  const { data, isLoading, mutate } = useSWR<PortfolioSummary>(
    "/api/portfolio",
    fetcher,
    { revalidateOnFocus: false },
  );

  const records = data?.records ?? [];

  async function handleDelete(id: number) {
    if (!confirm("确认删除这条记录？")) return;
    try {
      await apiFetch(`/api/portfolio/${id}`, { method: "DELETE" });
      mutate();
    } catch {
      /* ignore */
    }
  }

  const totalCost = data?.total_cost;
  const totalValue = data?.total_value;
  const totalPnl = data?.total_pnl ?? data?.realized_pnl;

  return (
    <div className="flex flex-col gap-6">
      <div className="surface px-5 py-4 md:px-6 md:py-5 flex items-center justify-between gap-3 flex-wrap">
        <div className="space-y-1">
          <p
            className="mono text-[11px] tracking-[0.08em]"
            style={{ color: "var(--text-dim)" }}
          >
            PORTFOLIO
          </p>
          <h1
            className="text-xl md:text-2xl font-semibold tracking-tight"
            style={{ color: "var(--text)" }}
          >
            持仓与交易流水
          </h1>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="btn-primary flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors active:scale-[0.98]"
          style={{ cursor: "pointer" }}
        >
          <Plus size={14} weight="bold" /> 添加记录
        </button>
      </div>

      {/* Summary tiles */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile
          label="总成本"
          value={totalCost != null ? fmtAmount(totalCost) : "-"}
        />
        <StatTile
          label="持仓市值"
          value={totalValue != null ? fmtAmount(totalValue) : "-"}
        />
        <StatTile
          label="总盈亏"
          value={totalPnl != null ? fmtAmount(totalPnl) : "-"}
          valueColor={
            totalPnl != null
              ? totalPnl >= 0
                ? "var(--up)"
                : "var(--down)"
              : undefined
          }
        />
        <StatTile label="记录数" value={String(records.length)} />
      </section>

      {/* Records table */}
      <section>
        {isLoading ? (
          <div className="flex flex-col gap-2">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} style={{ height: "44px" }} />
            ))}
          </div>
        ) : records.length === 0 ? (
          <div
            className="flex flex-col items-center gap-3 py-16"
            style={{ color: "var(--text-dim)" }}
          >
            <p className="text-sm">暂无记录。点击右上角添加第一条交易记录。</p>
          </div>
        ) : (
          <div
            className="surface-flat"
            style={{ borderRadius: "var(--radius)", overflow: "hidden" }}
          >
            {/* Header */}
            <div
              className="grid text-xs mono px-4 py-2"
              style={{
                gridTemplateColumns: "80px 100px 80px 80px 80px 1fr 40px",
                background: "var(--bg-strong)",
                borderBottom: "1px solid var(--border)",
                color: "var(--text-dim)",
              }}
            >
              <span>代码</span>
              <span>日期</span>
              <span>方向</span>
              <span>价格</span>
              <span>数量</span>
              <span>金额</span>
              <span></span>
            </div>

            {records.map((r, i) => (
              <div
                key={r.id}
                className="grid items-center px-4 py-2.5 text-sm hover:bg-[var(--bg-raised)] transition-colors"
                style={{
                  gridTemplateColumns: "80px 100px 80px 80px 80px 1fr 40px",
                  borderBottom:
                    i < records.length - 1 ? "1px solid var(--border)" : "none",
                }}
              >
                <span style={{ color: "var(--text)" }}>
                  {r.name || r.code}
                  <span
                    className="block mono text-xs"
                    style={{ color: "var(--text-dim)" }}
                  >
                    {r.code}
                  </span>
                </span>
                <span
                  className="mono text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  {r.trade_date}
                </span>
                <span
                  className="text-xs px-1.5 py-0.5 rounded inline-block"
                  style={{
                    background:
                      r.direction === "buy"
                        ? "color-mix(in srgb,var(--up) 12%,transparent)"
                        : "color-mix(in srgb,var(--down) 12%,transparent)",
                    color: r.direction === "buy" ? "var(--up)" : "var(--down)",
                    border: `1px solid ${r.direction === "buy" ? "color-mix(in srgb,var(--up) 30%,transparent)" : "color-mix(in srgb,var(--down) 30%,transparent)"}`,
                  }}
                >
                  {r.direction === "buy" ? "买入" : "卖出"}
                </span>
                <span className="mono text-xs" style={{ color: "var(--text)" }}>
                  {r.price.toFixed(2)}
                </span>
                <span className="mono text-xs" style={{ color: "var(--text)" }}>
                  {r.quantity.toLocaleString()}
                </span>
                <span>
                  <span
                    className="mono text-xs"
                    style={{ color: "var(--text)" }}
                  >
                    {fmtAmount(r.amount)}
                  </span>
                  {r.pnl != null && (
                    <span className={`ml-2 mono text-xs ${changeColor(r.pnl)}`}>
                      {fmtPct(r.pnl_pct)}
                    </span>
                  )}
                </span>
                <button
                  onClick={() => handleDelete(r.id)}
                  className="flex items-center justify-center w-7 h-7 rounded transition-colors hover:bg-[color-mix(in_srgb,var(--danger)_15%,transparent)]"
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-dim)",
                  }}
                  title="删除"
                >
                  <Trash size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {showForm && (
        <AddTradeForm
          onClose={() => setShowForm(false)}
          onSuccess={() => mutate()}
        />
      )}
    </div>
  );
}
