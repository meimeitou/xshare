const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8080";

export async function apiFetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const hasBody = init?.body != null && method !== "GET" && method !== "HEAD";

  const headers = new Headers(init?.headers);
  if (hasBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/** SWR fetcher */
export const fetcher = <T = unknown>(url: string): Promise<T> => apiFetch<T>(url);

export interface PerCodeCoverage {
  total: number;
  sufficient_count: number;
  under: number;
  rows_in_window: number;
  total_expected: number;
  ratio: number;
  threshold: number;
  sufficient: boolean;
  listed_in_window?: number;
  seasoned_total?: number;
  window_start?: string | null;
}

export interface DailyCoverage {
  trading_days_in_db: number;
  target_days: number;
  missing_estimate: number;
  oldest?: string | null;
  newest?: string | null;
  sufficient: boolean;
  per_code?: PerCodeCoverage;
  per_stock?: PerCodeCoverage;
}

export interface CoverageBundle {
  stock: DailyCoverage;
  index: DailyCoverage;
  fund: DailyCoverage;
}

export interface SyncEnqueueOptions {
  days?: number;
  pages?: number;
  retain_days?: number;
  backfill?: boolean;
  start_date?: string;
  end_date?: string;
  overwrite?: boolean;
}

export function getSyncCoverage() {
  return apiFetch<CoverageBundle>("/api/sync/coverage");
}

export function enqueueSyncJob(job: string, opts?: SyncEnqueueOptions) {
  return apiFetch(`/api/sync/jobs/${job}/enqueue`, {
    method: "POST",
    body: JSON.stringify(opts ?? {}),
  });
}

export function enqueueAllSyncJobs(opts?: SyncEnqueueOptions) {
  return apiFetch("/api/sync/jobs/all/enqueue", {
    method: "POST",
    body: JSON.stringify(opts ?? {}),
  });
}

export function cancelSyncTask(taskId: number) {
  return apiFetch(`/api/sync/tasks/${taskId}/cancel`, { method: "POST" });
}

export function cleanupSyncHistory(retainDays: number, retainCount = 500) {
  return apiFetch("/api/sync/history/cleanup", {
    method: "POST",
    body: JSON.stringify({ retain_days: retainDays, retain_count: retainCount }),
  });
}


export interface ChatEvent {
  type: "token" | "tool_call" | "tool_result" | "done" | "error";
  content?: string;
  name?: string;
  args?: string;
  result?: string;
  message?: string;
}

export async function* streamChat(
  sessionId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const resp = await fetch(`${BASE}/api/ai/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal,
  });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text().catch(() => resp.statusText)}`);
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try { yield JSON.parse(payload) as ChatEvent; } catch { /* skip malformed */ }
    }
  }
}


export interface ChatSession {
  session_id: string;
  title: string;
  message_count: number;
}

export function getSessions() {
  return apiFetch<ChatSession[]>("/api/ai/sessions");
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  toolCalls?: { name: string; args: string; result: string }[];
}

export function getHistory(sessionId: string) {
  return apiFetch<HistoryMessage[]>(`/api/ai/sessions/${sessionId}/history`);
}


export function deleteSession(sessionId: string) {
  return apiFetch<{ deleted: boolean }>(`/api/ai/sessions/${sessionId}`, { method: "DELETE" });
}
