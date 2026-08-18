"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { AnimatePresence, motion } from "motion/react";
import {
  fetcher,
  apiFetch,
  enqueueSyncJob,
  enqueueAllSyncJobs,
  cancelSyncTask,
  cleanupSyncHistory,
  type CoverageBundle,
  type DailyCoverage,
} from "@/lib/api";
import { fmtInterval } from "@/lib/format";
import { Skeleton } from "@/components/Skeleton";
import {
  Play,
  Stop,
  Check,
  X,
  Warning,
  ArrowsClockwise,
  Trash,
  ClockCounterClockwise,
  Calendar,
} from "@phosphor-icons/react";

/* ------ Types ---------------------------------------------------------------- */
interface SyncJobStatus {
  job: string;
  label?: string;
  enabled: boolean;
  interval_minutes?: number;
  schedule?: string;
  last_run_at?: string;
  next_run_at?: string | null;
  last_status?: string;
  last_error?: string;
  description?: string;
  params_schema?: Record<string, { default?: number | boolean; description?: string }>;
}
type QueueCounts = Record<string, number>;
interface QueueTask {
  id: number;
  task_type?: string;
  job?: string;
  status: string;
  trigger?: string;
  priority?: number;
  payload?: Record<string, unknown>;
  attempts?: number;
  queued_at?: string;
  started_at?: string;
}
interface SyncJobsResponse {
  jobs?: SyncJobStatus[];
  queue?: { counts?: QueueCounts; recent?: QueueTask[] };
}
interface HistoryItem {
  id?: number;
  job?: string;
  task_type?: string;
  status: string;
  trigger?: string;
  attempts?: number;
  payload?: Record<string, unknown>;
  started_at?: string;
  finished_at?: string;
  duration_s?: number;
  records?: number;
  error?: string;
  last_error?: string;
  result?: { synced?: number };
}

function formatError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function fmtTs(value?: string | null): string {
  if (!value) return "-";
  const d = new Date(value.includes("T") ? value : value.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/* ------ Status Badge ------------------------------------------------------- */
function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { color: string; icon: React.ReactNode }> = {
    success: { color: "var(--success)", icon: <Check size={11} weight="bold" /> },
    ok: { color: "var(--success)", icon: <Check size={11} weight="bold" /> },
    error: { color: "var(--danger)", icon: <X size={11} weight="bold" /> },
    skipped: { color: "var(--text-muted)", icon: null },
    running: {
      color: "var(--accent)",
      icon: <ArrowsClockwise size={11} className="animate-spin" />,
    },
    queued: { color: "var(--text-muted)", icon: null },
    cancelled: { color: "var(--text-dim)", icon: null },
    pending: { color: "var(--text-muted)", icon: null },
    disabled: { color: "var(--text-dim)", icon: null },
  };
  const c = config[status] ?? config.pending;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] mono whitespace-nowrap"
      style={{
        background: `color-mix(in srgb, ${c.color} 12%, transparent)`,
        color: c.color,
        border: `1px solid color-mix(in srgb, ${c.color} 30%, transparent)`,
      }}
    >
      {c.icon}
      {status}
    </span>
  );
}

/* ------ Coverage Bar ------------------------------------------------------- */
function CoverageCard({
  coverage,
  label,
  codeLabel,
  onBackfill,
  loading,
}: {
  coverage?: DailyCoverage;
  label: string;
  codeLabel: string;
  onBackfill: () => void;
  loading: boolean;
}) {
  if (!coverage) return null;
  const syncStatus = coverage.sync_status ?? "unsynced";
  const isSynced = syncStatus === "synced";
  const pct = Math.min(
    100,
    Math.round((coverage.trading_days_in_db / coverage.target_days) * 100),
  );
  const perCode = coverage.per_code ?? coverage.per_stock;
  const syncLabel =
    syncStatus === "synced"
      ? "已完成"
      : syncStatus === "error"
        ? "同步失败"
        : "未同步";
  return (
    <div
      className="surface px-4 py-3 flex flex-col gap-2"
      style={{ border: "1px solid var(--border)", borderRadius: "var(--radius)" }}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <p className="text-xs" style={{ color: "var(--text-dim)" }}>
            {label}
          </p>
          <p className="mono text-sm font-medium" style={{ color: "var(--text)" }}>
            {isSynced
              ? (coverage.latest_trade_date ?? coverage.newest ?? "")
              : `${coverage.trading_days_in_db} / ${coverage.target_days} 交易日`}
            <span
              className="ml-2 text-xs"
              style={{ color: isSynced ? "var(--success)" : "var(--danger)" }}
            >
              {syncLabel}
            </span>
          </p>
        </div>
        <button
          onClick={onBackfill}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded transition-colors"
          style={{
            background: "color-mix(in srgb,var(--accent) 15%,transparent)",
            border: "1px solid color-mix(in srgb,var(--accent) 30%,transparent)",
            color: "var(--accent)",
            cursor: loading ? "not-allowed" : "pointer",
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "入队中..." : "补全"}
        </button>
      </div>
      <div
        className="h-1.5 rounded-full overflow-hidden"
        style={{ background: "var(--bg-raised)" }}
      >
        <div
          className="h-full transition-all"
          style={{
            width: `${isSynced ? 100 : pct}%`,
            background: isSynced ? "var(--success)" : "var(--accent)",
          }}
        />
      </div>
      {perCode && perCode.total > 0 && (
        <p className="text-[11px] mono" style={{ color: "var(--text-dim)" }}>
          {codeLabel}覆盖: {perCode.sufficient_count}/{
            perCode.seasoned_total ?? perCode.total
          }{" "}
          达标
          {perCode.listed_in_window && perCode.listed_in_window > 0 && (
            <span>
              {" "}- 次新 {perCode.listed_in_window} 只
            </span>
          )}
          {" "}· 整体 {Math.round(perCode.ratio * 100)}%
        </p>
      )}
    </div>
  );
}

function CoveragePanel({
  bundle,
  onBackfill,
  loading,
}: {
  bundle?: CoverageBundle;
  onBackfill: (job: string) => void;
  loading: string | null;
}) {
  if (!bundle) return null;
  return (
    <div className="flex flex-col gap-3">
      <CoverageCard
        coverage={bundle.stock}
        label="股票日线覆盖率"
        codeLabel="个股"
        onBackfill={() => onBackfill("daily")}
        loading={loading === "daily"}
      />
      <CoverageCard
        coverage={bundle.index}
        label="指数日线覆盖率"
        codeLabel="指数"
        onBackfill={() => onBackfill("index_daily")}
        loading={loading === "index_daily"}
      />
      <CoverageCard
        coverage={bundle.fund}
        label="ETF 日线覆盖率"
        codeLabel="ETF"
        onBackfill={() => onBackfill("fund_daily")}
        loading={loading === "fund_daily"}
      />
    </div>
  );
}

/* ------ Shared table shell ------------------------------------------------- */
function DataTable({
  columns,
  children,
  empty,
}: {
  columns: string[];
  children: React.ReactNode;
  empty?: boolean;
}) {
  return (
    <div
      className="overflow-x-auto"
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--bg-panel)",
      }}
    >
      <table className="w-full text-left border-collapse" style={{ minWidth: 720 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {columns.map((col) => (
              <th
                key={col}
                className="px-3 py-2.5 text-[11px] font-medium tracking-wide whitespace-nowrap"
                style={{ color: "var(--text-dim)", background: "var(--bg-raised)" }}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {empty ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-8 text-sm text-center"
                style={{ color: "var(--text-dim)" }}
              >
                暂无数据
              </td>
            </tr>
          ) : (
            children
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ------ Jobs table --------------------------------------------------------- */
function JobsTable({
  jobs,
  loading,
  onRefresh,
  onError,
  onOpenHistory,
}: {
  jobs: SyncJobStatus[];
  loading: boolean;
  onRefresh: () => void;
  onError: (msg: string) => void;
  onOpenHistory: (job: string) => void;
}) {
  const [busyJob, setBusyJob] = useState<string | null>(null);

  async function runJob(job: SyncJobStatus, backfill = false) {
    setBusyJob(job.job);
    try {
      const opts: Record<string, unknown> = {};
      if (job.job === "daily" || job.job === "index_daily" || job.job === "fund_daily") {
        opts.days = 252;
        if (backfill) opts.backfill = true;
      }
      if (job.job === "news") {
        opts.pages = 3;
        opts.retain_days = 1;
      }
      await enqueueSyncJob(job.job, opts);
      onRefresh();
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusyJob(null);
    }
  }

  async function toggleEnabled(job: SyncJobStatus) {
    setBusyJob(job.job);
    try {
      await apiFetch(`/api/sync/jobs/${job.job}/config`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !job.enabled }),
      });
      onRefresh();
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusyJob(null);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-2">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} style={{ height: 44 }} />
        ))}
      </div>
    );
  }

  return (
    <DataTable
      columns={["任务", "状态", "调度", "上次运行", "下次执行", "操作"]}
      empty={jobs.length === 0}
    >
      {jobs.map((job) => {
        const busy = busyJob === job.job;
        const scheduleLabel =
          job.schedule === "calendar_1700"
            ? "交易日 17:00"
          : `每 ${fmtInterval(job.interval_minutes)}`;
        return (
          <tr
            key={job.job}
            style={{ borderBottom: "1px solid var(--border)" }}
          >
            <td className="px-3 py-3 align-top">
              <div className="flex flex-col gap-0.5 min-w-[140px]">
                <span className="text-sm font-medium" style={{ color: "var(--text)" }}>
                  {job.label ?? job.job}
                </span>
                <span className="mono text-[11px]" style={{ color: "var(--text-dim)" }}>
                  {job.job}
                </span>
                {job.last_error && (
                  <span className="text-[11px] truncate max-w-[220px]" style={{ color: "var(--danger)" }}>
                    {job.last_error}
                  </span>
                )}
              </div>
            </td>
            <td className="px-3 py-3 align-top">
              <div className="flex flex-col gap-1">
                {job.last_status ? (
                  <StatusBadge status={job.last_status} />
                ) : (
                  <span className="text-xs" style={{ color: "var(--text-dim)" }}>
                    -
                  </span>
                )}
                <span
                  className="text-[11px]"
                style={{ color: job.enabled ? "var(--success)" : "var(--text-dim)" }}
                >
                  {job.enabled ? "已启用" : "已停用"}
                </span>
              </div>
            </td>
            <td className="px-3 py-3 align-top">
              <span className="mono text-xs" style={{ color: "var(--text-muted)" }}>
                {scheduleLabel}
              </span>
            </td>
            <td className="px-3 py-3 align-top">
              <span className="mono text-xs" style={{ color: "var(--text-muted)" }}>
                {fmtTs(job.last_run_at)}
              </span>
            </td>
            <td className="px-3 py-3 align-top">
              <span
                className="mono text-xs"
                style={{ color: job.enabled ? "var(--accent)" : "var(--text-dim)" }}
              >
                {job.enabled ? fmtTs(job.next_run_at) : "已停止"}
              </span>
            </td>
            <td className="px-3 py-3 align-top">
              <div className="flex items-center gap-1.5 flex-wrap">
                <button
                  onClick={() => toggleEnabled(job)}
                  disabled={busy}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px]"
                  style={{
                    background: job.enabled
                      ? "color-mix(in srgb,var(--danger) 12%,var(--bg-raised))"
                      : "color-mix(in srgb,var(--accent) 15%,var(--bg-raised))",
                    border: "1px solid var(--border)",
                    color: job.enabled ? "var(--danger)" : "var(--accent)",
                    cursor: busy ? "not-allowed" : "pointer",
                  }}
                >
                  {job.enabled ? <Stop size={11} weight="fill" /> : <Play size={11} weight="fill" />}
                  {job.enabled ? "停止" : "启动"}
                </button>
                <button
                  onClick={() => runJob(job, false)}
                  disabled={busy}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px]"
                  style={{
                    background: "color-mix(in srgb,var(--accent) 15%,var(--bg-raised))",
                    border: "1px solid color-mix(in srgb,var(--accent) 30%,var(--border))",
                    color: "var(--accent)",
                    cursor: busy ? "not-allowed" : "pointer",
                  }}
                >
                  {busy ? (
                    <ArrowsClockwise size={11} className="animate-spin" />
                  ) : (
                    <Play size={11} weight="fill" />
                  )}
                  运行
                </button>
                {(job.job === "daily" || job.job === "index_daily" || job.job === "fund_daily") && (
                  <button
                    onClick={() => runJob(job, true)}
                    disabled={busy}
                    className="px-2 py-1 rounded text-[11px]"
                    style={{
                      border: "1px solid var(--border)",
                      color: "var(--text-muted)",
                      background: "var(--bg-raised)",
                      cursor: busy ? "not-allowed" : "pointer",
                    }}
                  >
                    补数
                  </button>
                )}
                <button
                  onClick={() => onOpenHistory(job.job)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px]"
                  style={{
                    border: "1px solid var(--border)",
                    color: "var(--text-muted)",
                    background: "var(--bg-raised)",
                    cursor: "pointer",
                  }}
                >
                  <ClockCounterClockwise size={11} />
                  日志
                </button>
              </div>
            </td>
          </tr>
        );
      })}
    </DataTable>
  );
}

/* ------ Queue table -------------------------------------------------------- */
function QueueTable({
  counts,
  recent,
  onCancel,
  onRefresh,
}: {
  counts: QueueCounts;
  recent: QueueTask[];
  onCancel: (id: number) => void;
  onRefresh: () => void;
}) {
  const summary = [
    { key: "queued", label: "排队", color: "var(--text-muted)" },
    { key: "running", label: "执行中", color: "var(--accent)" },
    { key: "success", label: "成功", color: "var(--success)" },
    { key: "skipped", label: "跳过", color: "var(--text-muted)" },
    { key: "error", label: "失败", color: "var(--danger)" },
    { key: "cancelled", label: "已取消", color: "var(--text-dim)" },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        {summary.map((item) => (
          <div
            key={item.key}
            className="flex items-center gap-1.5 px-2 py-1 rounded text-xs mono"
            style={{
              background: "var(--bg-raised)",
              border: "1px solid var(--border)",
              color: "var(--text-muted)",
            }}
          >
            <span>{item.label}</span>
            <span style={{ color: item.color }}>{counts[item.key] ?? 0}</span>
          </div>
        ))}
        <button
          onClick={onRefresh}
          className="text-xs px-2 py-1 rounded"
          style={{
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
            background: "none",
            cursor: "pointer",
          }}
        >
          刷新
        </button>
      </div>

      <DataTable
        columns={["ID", "任务", "状态", "触发", "优先级", "入队时间", "操作"]}
        empty={recent.length === 0}
      >
        {recent.map((t) => (
          <tr key={t.id} style={{ borderBottom: "1px solid var(--border)" }}>
            <td className="px-3 py-2.5 mono text-xs" style={{ color: "var(--text-dim)" }}>
              #{t.id}
            </td>
            <td className="px-3 py-2.5 mono text-xs" style={{ color: "var(--text)" }}>
              {t.task_type ?? t.job ?? "-"}
            </td>
            <td className="px-3 py-2.5">
              <StatusBadge status={t.status} />
            </td>
            <td className="px-3 py-2.5 mono text-xs" style={{ color: "var(--text-dim)" }}>
              {t.trigger ?? "-"}
            </td>
            <td className="px-3 py-2.5 mono text-xs" style={{ color: "var(--text-muted)" }}>
              {t.priority ?? "-"}
            </td>
            <td className="px-3 py-2.5 mono text-xs" style={{ color: "var(--text-muted)" }}>
              {fmtTs(t.queued_at)}
            </td>
            <td className="px-3 py-2.5">
              {t.status === "queued" ? (
                <button
                  onClick={() => onCancel(t.id)}
                  className="text-[11px] px-2 py-0.5 rounded"
                  style={{
                    border: "1px solid var(--border)",
                    color: "var(--danger)",
                    background: "none",
                    cursor: "pointer",
                  }}
                >
                  取消
                </button>
              ) : (
                <span className="text-[11px]" style={{ color: "var(--text-dim)" }}>
                  -
                </span>
              )}
            </td>
          </tr>
        ))}
      </DataTable>
    </div>
  );
}

/* ------ History drawer ----------------------------------------------------- */
function HistoryDrawer({
  open,
  jobFilter,
  jobLabel,
  onClose,
}: {
  open: boolean;
  jobFilter: string | null;
  jobLabel?: string;
  onClose: () => void;
}) {
  const url = jobFilter
    ? `/api/sync/history?job=${jobFilter}&limit=50`
    : null;

  const { data, isLoading } = useSWR<HistoryItem[] | { history?: HistoryItem[] }>(
    open && url ? url : null,
    fetcher,
    { refreshInterval: 10_000, revalidateOnFocus: false },
  );

  const items: HistoryItem[] = Array.isArray(data)
    ? data
    : ((data as { history?: HistoryItem[] })?.history ?? []);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && jobFilter && (
        <>
          <motion.div
            className="fixed inset-0 z-40"
            style={{ background: "rgba(10, 16, 24, 0.45)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed right-0 top-0 z-50 h-full w-full max-w-md flex flex-col"
            style={{
              background: "var(--bg-elevated)",
              borderLeft: "1px solid var(--border)",
              boxShadow: "var(--shadow-hard)",
            }}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 380, damping: 36 }}
          >
            <div
              className="flex items-center justify-between gap-3 px-4 py-3"
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <div>
                <p className="text-[11px] mono tracking-wide" style={{ color: "var(--text-dim)" }}>
                  RUN HISTORY
                </p>
                <h2 className="text-sm font-semibold" style={{ color: "var(--text)" }}>
                  {jobLabel ?? jobFilter}
                </h2>
              </div>
              <button
                onClick={onClose}
                className="p-1.5 rounded"
                style={{
                  border: "1px solid var(--border)",
                  background: "var(--bg-raised)",
                  color: "var(--text-muted)",
                  cursor: "pointer",
                }}
              >
                <X size={14} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-3 py-3">
              {isLoading ? (
                <div className="flex flex-col gap-2">
                  {[...Array(6)].map((_, i) => (
                    <Skeleton key={i} style={{ height: 56 }} />
                  ))}
                </div>
              ) : items.length === 0 ? (
                <p className="text-sm px-1 py-6 text-center" style={{ color: "var(--text-dim)" }}>
                  暂无历史记录
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {items.map((h, i) => {
                    const records = h.records ?? h.result?.synced;
                    const errorText = h.error ?? h.last_error;
                    const duration =
                      h.duration_s ??
                      (h.started_at && h.finished_at
                        ? (new Date(h.finished_at).getTime() -
                            new Date(h.started_at).getTime()) /
                          1000
                        : null);
                    return (
                      <div
                        key={h.id ?? i}
                        className="px-3 py-2.5 rounded"
                        style={{
                          background: "var(--bg-panel)",
                          border: "1px solid var(--border)",
                        }}
                      >
                        <div className="flex items-center gap-2 flex-wrap">
                          {h.status === "success" || h.status === "ok" ? (
                            <Check size={13} style={{ color: "var(--success)" }} weight="bold" />
                          ) : h.status === "error" ? (
                            <Warning size={13} style={{ color: "var(--danger)" }} />
                          ) : h.status === "running" ? (
                            <ArrowsClockwise size={13} style={{ color: "var(--accent)" }} />
                          ) : (
                            <div
                              style={{
                                width: 13,
                                height: 13,
                                borderRadius: "50%",
                                background: "var(--text-dim)",
                              }}
                            />
                          )}
                          <StatusBadge status={h.status} />
                          {h.trigger && (
                            <span className="mono text-[11px]" style={{ color: "var(--text-dim)" }}>
                              {h.trigger}
                            </span>
                          )}
                          {records != null && (
                            <span className="mono text-[11px]" style={{ color: "var(--text-dim)" }}>
                              {records} 条
                            </span>
                          )}
                          {duration != null && (
                            <span className="mono text-[11px]" style={{ color: "var(--text-dim)" }}>
                              {duration.toFixed(1)}s
                            </span>
                          )}
                        </div>
                        {h.payload && Object.keys(h.payload).length > 0 && (
                          <p className="text-[11px] mono mt-1" style={{ color: "var(--text-dim)" }}>
                            {JSON.stringify(h.payload)}
                          </p>
                        )}
                        {errorText && (
                          <p className="text-[11px] mt-1 break-all" style={{ color: "var(--danger)" }}>
                            {errorText}
                          </p>
                        )}
                        <p className="text-[11px] mono mt-1" style={{ color: "var(--text-dim)" }}>
                          {fmtTs(h.started_at ?? h.finished_at)}
                        </p>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

/* ------ Page --------------------------------------------------------------- */
export default function SyncPage() {
  const [error, setError] = useState<string | null>(null);
  const [backfillLoading, setBackfillLoading] = useState<string | null>(null);
  const [backfillStart, setBackfillStart] = useState(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 1);
    return d.toISOString().slice(0, 10);
  });
  const [backfillEnd, setBackfillEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [backfillOverwrite, setBackfillOverwrite] = useState(true);
  const [cleanupDays, setCleanupDays] = useState("30");
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [enqueueAllLoading, setEnqueueAllLoading] = useState(false);
  const [historyJob, setHistoryJob] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR<SyncJobsResponse>(
    "/api/sync/jobs",
    fetcher,
    { refreshInterval: 15_000, revalidateOnFocus: false },
  );

  const { data: coverage, mutate: mutateCoverage } = useSWR<CoverageBundle>(
    "/api/sync/coverage",
    fetcher,
    { refreshInterval: 30_000, revalidateOnFocus: false },
  );

  const jobs: SyncJobStatus[] = data?.jobs ?? [];
  const queueCounts: QueueCounts = data?.queue?.counts ?? {};
  const recent: QueueTask[] = data?.queue?.recent ?? [];

  const historyLabel = useMemo(() => {
    if (!historyJob) return undefined;
    return jobs.find((j) => j.job === historyJob)?.label ?? historyJob;
  }, [historyJob, jobs]);

  async function handleBackfill(job: string) {
    setBackfillLoading(job);
    setError(null);
    try {
      const opts: Record<string, unknown> = {};
      if (backfillStart && backfillEnd) {
        opts.start_date = backfillStart;
        opts.end_date = backfillEnd;
        opts.overwrite = backfillOverwrite;
      } else {
        opts.days = 252;
        opts.backfill = true;
      }
      await enqueueSyncJob(job, opts);
      mutate();
      mutateCoverage();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setBackfillLoading(null);
    }
  }

  async function handleCancel(taskId: number) {
    try {
      await cancelSyncTask(taskId);
      mutate();
    } catch (e) {
      setError(formatError(e));
    }
  }

  async function handleCleanup() {
    const d = parseInt(cleanupDays);
    if (isNaN(d) || d < 1) {
      setError("保留天数必须为正整数");
      return;
    }
    setCleanupLoading(true);
    try {
      await cleanupSyncHistory(d);
      mutate();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setCleanupLoading(false);
    }
  }

  async function handleEnqueueAll() {
    setEnqueueAllLoading(true);
    try {
      await enqueueAllSyncJobs();
      mutate();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setEnqueueAllLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="surface px-5 py-4 md:px-6 md:py-5">
        <p className="mono text-[11px] tracking-[0.08em]" style={{ color: "var(--text-dim)" }}>
          SYNC CENTER
        </p>
        <div className="mt-1 flex items-center justify-between gap-3 flex-wrap">
          <h1
            className="text-xl md:text-2xl font-semibold tracking-tight"
            style={{ color: "var(--text)" }}
          >
            数据同步与任务调度
          </h1>
          <button
            onClick={handleEnqueueAll}
            disabled={enqueueAllLoading}
            className="text-xs px-3 py-1.5 rounded"
            style={{
              background: "var(--bg-raised)",
              border: "1px solid var(--border)",
              color: "var(--text-muted)",
              cursor: enqueueAllLoading ? "not-allowed" : "pointer",
            }}
          >
            {enqueueAllLoading ? "入队中..." : "全部入队"}
          </button>
        </div>
      </div>

      {error && (
        <div
          className="px-4 py-3 text-sm rounded flex items-center justify-between gap-2"
          style={{
            background: "color-mix(in srgb,var(--danger) 10%,transparent)",
            border: "1px solid color-mix(in srgb,var(--danger) 30%,transparent)",
            color: "var(--danger)",
          }}
        >
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            style={{ background: "none", border: "none", cursor: "pointer", color: "inherit" }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      <CoveragePanel
        bundle={coverage}
        onBackfill={handleBackfill}
        loading={backfillLoading}
      />

      <div
        className="px-4 py-3 flex flex-col gap-2"
        style={{
          background: "var(--bg-panel)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
        }}
      >
        <div className="flex items-center gap-2">
          <Calendar size={14} style={{ color: "var(--accent)" }} />
          <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
            一次性补全
          </span>
          <span className="text-[11px]" style={{ color: "var(--text-dim)" }}>
            默认补全最近一年，可调整日期；勾选「覆盖已有」则强制重拉
          </span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="date"
            className="text-xs mono py-1 px-2 rounded outline-none"
            style={{
              background: "var(--bg-raised)",
              border: "1px solid var(--border)",
              color: "var(--text)",
            }}
            value={backfillStart}
            onChange={(e) => setBackfillStart(e.target.value)}
          />
          <span className="text-xs" style={{ color: "var(--text-dim)" }}>
            至
          </span>
          <input
            type="date"
            className="text-xs mono py-1 px-2 rounded outline-none"
            style={{
              background: "var(--bg-raised)",
              border: "1px solid var(--border)",
              color: "var(--text)",
            }}
            value={backfillEnd}
            onChange={(e) => setBackfillEnd(e.target.value)}
          />
          <label
            className="flex items-center gap-1 text-[11px] cursor-pointer"
            style={{ color: "var(--text-muted)" }}
          >
            <input
              type="checkbox"
              checked={backfillOverwrite}
              onChange={(e) => setBackfillOverwrite(e.target.checked)}
              className="cursor-pointer"
            />
            覆盖已有
          </label>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11px]" style={{ color: "var(--text-dim)" }}>
            点击下方卡片的「补全」按钮触发所选区间
          </span>
        </div>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium" style={{ color: "var(--text-muted)" }}>
          同步任务
        </h2>
        <JobsTable
          jobs={jobs}
          loading={isLoading}
          onRefresh={() => {
            mutate();
            mutateCoverage();
          }}
          onError={setError}
          onOpenHistory={setHistoryJob}
        />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium" style={{ color: "var(--text-muted)" }}>
          执行队列
        </h2>
        <QueueTable
          counts={queueCounts}
          recent={recent}
          onCancel={handleCancel}
          onRefresh={() => mutate()}
        />
      </section>

      <div
        className="px-4 py-3 flex items-center gap-3 flex-wrap"
        style={{
          background: "var(--bg-panel)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
        }}
      >
        <Trash size={14} style={{ color: "var(--text-dim)" }} />
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          日志清理
        </span>
        <input
          type="number"
          min="1"
          className="w-16 text-xs mono py-1 px-2 rounded outline-none"
          style={{
            background: "var(--bg-raised)",
            border: "1px solid var(--border)",
            color: "var(--text)",
          }}
          value={cleanupDays}
          onChange={(e) => setCleanupDays(e.target.value)}
        />
        <span className="text-xs" style={{ color: "var(--text-dim)" }}>
          天前
        </span>
        <button
          onClick={handleCleanup}
          disabled={cleanupLoading}
          className="text-xs px-2 py-1 rounded"
          style={{
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
            background: "var(--bg-raised)",
            cursor: cleanupLoading ? "not-allowed" : "pointer",
          }}
        >
          {cleanupLoading ? "清理中..." : "清理"}
        </button>
      </div>

      <HistoryDrawer
        open={!!historyJob}
        jobFilter={historyJob}
        jobLabel={historyLabel}
        onClose={() => setHistoryJob(null)}
      />
    </div>
  );
}
