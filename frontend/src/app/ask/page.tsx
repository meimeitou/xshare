"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  streamChat,
  getSessions,
  getHistory,
  deleteSession as deleteSessionApi,
  type ChatSession,
  type HistoryMessage,
} from "@/lib/api";
import {
  PaperPlaneTilt,
  X,
  Wrench,
  CaretDown,
  CaretRight,
  ChatCircleDots,
  Plus,
  List,
  Trash,
  Copy,
  Check,
} from "@phosphor-icons/react";

interface Message {
  role: "user" | "assistant";
  content: string;
  toolCalls?: { name: string; args: string; result: string }[];
  streaming?: boolean;
}

const PRESET_QUESTIONS = [
  "今天大盘怎么样",
  "分析比亚迪",
  "最近有什么热门板块",
  "贵州茅台的PE是多少",
];

export default function AskPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<string>("");
  const [messagesBySession, setMessagesBySession] = useState<Record<string, Message[]>>({});
  const [loadingBySession, setLoadingBySession] = useState<Record<string, boolean>>({});
  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const abortRef = useRef<Record<string, AbortController | null>>({});
  const inflightRef = useRef<Set<string>>(new Set());
  const initializedRef = useRef<Set<string>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messages = useMemo(
    () => (activeSession ? messagesBySession[activeSession] ?? [] : []),
    [activeSession, messagesBySession],
  );
  const loading = activeSession ? loadingBySession[activeSession] ?? false : false;

  const startNewSession = useCallback(() => {
    const sid = crypto.randomUUID();
    setSessions((prev) => [
      { session_id: sid, title: "新对话", message_count: 0 },
      ...prev,
    ]);
    setActiveSession(sid);
    initializedRef.current.add(sid);
    setMessagesBySession((prev) => ({ ...prev, [sid]: [] }));
  }, []);


  /* On mount: load sessions, pick last active or create new */
  useEffect(() => {
    (async () => {
      try {
        const list = await getSessions();
        setSessions(list);
        if (list.length > 0) {
          const sid = list[0].session_id;
          setActiveSession(sid);
          const history = await getHistory(sid);
          initializedRef.current.add(sid);
          setMessagesBySession((prev) => ({ ...prev, [sid]: history.map(historyToMessage) }));
        } else {
          startNewSession();
        }
      } catch {
        startNewSession();
      }
    })();
  }, [startNewSession]);


  /* auto-scroll on new messages of the active session */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /* textarea auto-resize */
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [input]);


  const switchSession = useCallback(
    async (sid: string) => {
      setSidebarOpen(false);
      setActiveSession(sid);
      // Load history lazily; reuse cached messages (incl. live streaming updates) thereafter
      if (initializedRef.current.has(sid)) return;
      try {
        const history = await getHistory(sid);
        initializedRef.current.add(sid);
        setMessagesBySession((prev) => ({ ...prev, [sid]: history.map(historyToMessage) }));
      } catch {
        initializedRef.current.add(sid);
        setMessagesBySession((prev) => ({ ...prev, [sid]: [] }));
      }
    },
    [],
  );

  const deleteSession = useCallback(
    async (sid: string) => {
      // Abort any in-flight stream for this session, regardless of active view
      abortRef.current[sid]?.abort();
      delete abortRef.current[sid];
      inflightRef.current.delete(sid);
      setLoadingBySession((prev) => {
        if (!prev[sid]) return prev;
        const next = { ...prev };
        delete next[sid];
        return next;
      });
      // Call backend to delete session state
      try { await deleteSessionApi(sid); } catch { /* non-critical */ }
      initializedRef.current.delete(sid);
      setMessagesBySession((prev) => {
        if (!(sid in prev)) return prev;
        const next = { ...prev };
        delete next[sid];
        return next;
      });
      setSessions((prev) => prev.filter((s) => s.session_id !== sid));
      if (activeSession === sid) {
        const remaining = sessions.filter((s) => s.session_id !== sid);
        if (remaining.length > 0) {
          const nextSid = remaining[0].session_id;
          setActiveSession(nextSid);
          if (!initializedRef.current.has(nextSid)) {
            try {
              const history = await getHistory(nextSid);
              initializedRef.current.add(nextSid);
              setMessagesBySession((prev) => ({ ...prev, [nextSid]: history.map(historyToMessage) }));
            } catch {
              initializedRef.current.add(nextSid);
              setMessagesBySession((prev) => ({ ...prev, [nextSid]: [] }));
            }
          }
        } else {
          startNewSession();
        }
      }
    },
    [activeSession, sessions, startNewSession],
  );

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !activeSession) return;
      const sid = activeSession;
      // Guard against double-send on the same session (ref = synchronous, no stale state)
      if (inflightRef.current.has(sid)) return;
      inflightRef.current.add(sid);

      setInput("");
      setLoadingBySession((prev) => ({ ...prev, [sid]: true }));

      const userMsg: Message = { role: "user", content: trimmed };
      const assistantMsg: Message = { role: "assistant", content: "", streaming: true };
      setMessagesBySession((prev) => ({
        ...prev,
        [sid]: [...(prev[sid] ?? []), userMsg, assistantMsg],
      }));

      // Update session title if it was "新对话"
      setSessions((prev) =>
        prev.map((s) =>
          s.session_id === sid && s.title === "新对话"
            ? { ...s, title: trimmed.slice(0, 40), message_count: s.message_count + 2 }
            : s,
        ),
      );

      const ac = new AbortController();
      abortRef.current[sid] = ac;

      try {
        for await (const ev of streamChat(sid, trimmed, ac.signal)) {
          setMessagesBySession((prev) => {
            const cur = prev[sid] ?? [];
            const next = [...cur];
            const last = next[next.length - 1];
            if (!last || last.role !== "assistant") return prev;

            switch (ev.type) {
              case "token":
                next[next.length - 1] = { ...last, content: last.content + (ev.content ?? "") };
                break;
              case "tool_call":
                next[next.length - 1] = {
                  ...last,
                  toolCalls: [
                    ...(last.toolCalls ?? []),
                    { name: ev.name ?? "", args: ev.args ?? "", result: "" },
                  ],
                };
                break;
              case "tool_result":
                if (last.toolCalls && last.toolCalls.length > 0) {
                  const tc = [...last.toolCalls];
                  tc[tc.length - 1] = { ...tc[tc.length - 1], result: ev.result ?? "" };
                  next[next.length - 1] = { ...last, toolCalls: tc };
                }
                break;
              case "done":
                next[next.length - 1] = { ...last, streaming: false };
                break;
              case "error":
                next[next.length - 1] = {
                  ...last,
                  content: last.content + "\n\n[错误] " + (ev.message ?? "未知错误"),
                  streaming: false,
                };
                break;
            }
            return { ...prev, [sid]: next };
          });
        }
        // Refresh session list to get updated counts
        try {
          const refreshed = await getSessions();
          setSessions(refreshed);
        } catch { /* non-critical */ }
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          setMessagesBySession((prev) => {
            const cur = prev[sid] ?? [];
            const next = [...cur];
            const last = next[next.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              next[next.length - 1] = { ...last, streaming: false, content: last.content + "\n\n[已取消]" };
            }
            return { ...prev, [sid]: next };
          });
        } else {
          setMessagesBySession((prev) => {
            const cur = prev[sid] ?? [];
            const next = [...cur];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                content: last.content + "\n\n[错误] " + (e as Error).message,
                streaming: false,
              };
            }
            return { ...prev, [sid]: next };
          });
        }
      } finally {
        inflightRef.current.delete(sid);
        setLoadingBySession((prev) => {
          if (!prev[sid]) return prev;
          const next = { ...prev };
          delete next[sid];
          return next;
        });
        if (abortRef.current[sid] === ac) delete abortRef.current[sid];
      }
    },
    [activeSession],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  const cancel = () => {
    if (activeSession) abortRef.current[activeSession]?.abort();
  };

  return (
    <div
      className="min-h-screen flex"
      style={{ background: "var(--bg)", color: "var(--text)" }}
    >
      {/* Sidebar */}
      <SessionSidebar
        sessions={sessions}
        activeSession={activeSession}
        open={sidebarOpen}
        loadingBySession={loadingBySession}
        onNew={startNewSession}
        onSwitch={switchSession}
        onDelete={deleteSession}
        onClose={() => setSidebarOpen(false)}
      />

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div
          className="sticky top-0 z-10 px-4 md:px-6 py-4 flex items-center gap-3"
          style={{
            borderBottom: "1px solid var(--border)",
            background: "color-mix(in srgb, var(--bg) 86%, transparent)",
            backdropFilter: "blur(14px)",
          }}
        >
          <button
            onClick={() => setSidebarOpen(true)}
            className="md:hidden flex items-center justify-center"
            style={{
              width: 36,
              height: 36,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border)",
              background: "var(--bg-elevated)",
              color: "var(--text-muted)",
            }}
          >
            <List size={18} />
          </button>
          <ChatCircleDots size={24} weight="fill" style={{ color: "var(--accent)" }} />
          <div>
            <h1 className="text-lg font-bold" style={{ color: "var(--text)" }}>
              AI 问股
            </h1>
            <p className="text-xs" style={{ color: "var(--text-dim)" }}>
              自然语言提问，自动调用金融工具分析
            </p>
          </div>
          <div className="ml-auto md:hidden">
            <button
              onClick={startNewSession}
              className="flex items-center gap-1 px-3 py-1.5 text-xs"
              style={{
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border)",
                background: "var(--bg-elevated)",
                color: "var(--text-muted)",
              }}
            >
              <Plus size={14} weight="bold" />
              新对话
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 md:px-6 py-6">
          <div className="max-w-[900px] mx-auto flex flex-col gap-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center gap-4 py-12">
                <p className="text-sm" style={{ color: "var(--text-dim)" }}>
                  试试这些问题：
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {PRESET_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      onClick={() => send(q)}
                      className="px-4 py-2 text-sm transition-colors"
                      style={{
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--border)",
                        background: "var(--bg-elevated)",
                        color: "var(--text-muted)",
                      }}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} />
            ))}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input */}
        <div
          className="px-4 md:px-6 py-4"
          style={{
            borderTop: "1px solid var(--border)",
            background: "color-mix(in srgb, var(--bg) 86%, transparent)",
            backdropFilter: "blur(14px)",
          }}
        >
          <div className="max-w-[900px] mx-auto flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入你的问题…（Enter 发送，Shift+Enter 换行）"
              rows={1}
              className="flex-1 resize-none px-4 py-3 text-sm outline-none"
              style={{
                borderRadius: "var(--radius)",
                border: "1px solid var(--border)",
                background: "var(--bg-elevated)",
                color: "var(--text)",
                maxHeight: "160px",
              }}
            />
            {loading ? (
              <button
                onClick={cancel}
                className="flex items-center gap-1.5 px-4 py-3 text-sm font-medium transition-colors"
                style={{
                  borderRadius: "var(--radius)",
                  border: "1px solid var(--danger)",
                  background: "transparent",
                  color: "var(--danger)",
                }}
              >
                <X size={16} weight="bold" />
                取消
              </button>
            ) : (
              <button
                onClick={() => send(input)}
                disabled={!input.trim()}
                className="flex items-center gap-1.5 px-4 py-3 text-sm font-medium transition-opacity"
                style={{
                  borderRadius: "var(--radius)",
                  border: "none",
                  background: input.trim() ? "var(--accent)" : "var(--bg-strong)",
                  color: input.trim() ? "#fff" : "var(--text-dim)",
                  cursor: input.trim() ? "pointer" : "not-allowed",
                }}
              >
                <PaperPlaneTilt size={16} weight="fill" />
                发送
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------ Helpers ------------------------------------------------------------- */
function historyToMessage(h: HistoryMessage): Message {
  return {
    role: h.role,
    content: h.content,
    toolCalls: h.toolCalls,
  };
}

/* ------ Session sidebar ----------------------------------------------------- */
function SessionSidebar({
  sessions,
  activeSession,
  open,
  loadingBySession,
  onNew,
  onSwitch,
  onDelete,
  onClose,
}: {
  sessions: ChatSession[];
  activeSession: string;
  open: boolean;
  loadingBySession: Record<string, boolean>;
  onNew: () => void;
  onSwitch: (sid: string) => void;
  onDelete: (sid: string) => void;
  onClose: () => void;
}) {
  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-30 md:hidden"
          style={{ background: "rgba(0,0,0,0.4)" }}
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed md:sticky top-0 z-40 md:z-auto h-screen md:h-auto w-[260px] shrink-0 flex flex-col transition-transform ${
          open ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        }`}
        style={{
          background: "var(--bg-panel)",
          borderRight: "1px solid var(--border)",
        }}
      >
        {/* Sidebar header */}
        <div
          className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <span className="text-xs font-semibold" style={{ color: "var(--text-muted)" }}>
            会话历史
          </span>
          <button
            onClick={onClose}
            className="md:hidden"
            style={{ color: "var(--text-dim)" }}
          >
            <X size={16} />
          </button>
        </div>

        {/* New session button */}
        <div className="px-3 py-2">
          <button
            onClick={onNew}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium transition-colors"
            style={{
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--accent)",
              background: "var(--accent-soft)",
              color: "var(--accent-strong)",
            }}
          >
            <Plus size={14} weight="bold" />
            新对话
          </button>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {sessions.length === 0 && (
            <p className="px-3 py-4 text-xs text-center" style={{ color: "var(--text-dim)" }}>
              暂无会话
            </p>
          )}
          {sessions.map((s) => {
            const active = s.session_id === activeSession;
            return (
              <div
                key={s.session_id}
                className="group flex items-center gap-1 mb-0.5"
                style={{
                  borderRadius: "var(--radius-sm)",
                  background: active ? "var(--accent-soft)" : "transparent",
                }}
              >
                <button
                  onClick={() => onSwitch(s.session_id)}
                  className="flex-1 flex flex-col gap-0.5 px-3 py-2 text-left min-w-0"
                  style={{
                    color: active ? "var(--accent-strong)" : "var(--text-muted)",
                  }}
                >
                  <div className="flex items-center gap-1.5">
                    <span
                      className="text-sm truncate"
                      style={{ fontWeight: active ? 600 : 400 }}
                    >
                      {s.title || "(空对话)"}
                    </span>
                    {loadingBySession[s.session_id] && (
                      <span
                        className="inline-block w-3 h-3 rounded-full animate-spin shrink-0"
                        style={{
                          border: "1.5px solid var(--border)",
                          borderTopColor: "var(--accent)",
                        }}
                      />
                    )}
                  </div>
                  <span className="text-xs" style={{ color: "var(--text-dim)" }}>
                    {loadingBySession[s.session_id] ? "AI 思考中…" : `${s.message_count} 条消息`}
                  </span>
                </button>
                <button
                  onClick={() => onDelete(s.session_id)}
                  className="opacity-0 group-hover:opacity-100 px-1.5 mr-1 transition-opacity"
                  style={{ color: "var(--text-dim)" }}
                >
                  <Trash size={13} />
                </button>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}

/* ------ Message bubble ------------------------------------------------------ */
function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }, [msg.content]);

  const canCopy = !isUser && msg.content && !msg.streaming;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className="max-w-[85%] px-4 py-3"
        style={{
          borderRadius: "var(--radius)",
          background: isUser ? "var(--bg-raised)" : "var(--bg-panel)",
          border: isUser ? "none" : "1px solid var(--border)",
        }}
      >
        {/* Tool calls */}
        {!isUser && msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mb-2 flex flex-col gap-1">
            {msg.toolCalls.map((tc, i) => (
              <ToolCallItem key={i} tc={tc} />
            ))}
          </div>
        )}

        {/* Text content */}
        {msg.content && (
          <div className="text-sm">
            {isUser ? (
              <div className="whitespace-pre-wrap" style={{ color: "var(--text)" }}>
                {msg.content}
              </div>
            ) : (
              <div className="ai-markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            )}
            {msg.streaming && (
              <span
                className="inline-block w-1.5 h-4 ml-1 align-text-bottom"
                style={{ background: "var(--accent)", animation: "blink 1s infinite" }}
              />
            )}
          </div>
        )}

        {/* Copy button */}
        {canCopy && (
          <div className="mt-2 flex justify-end">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 text-xs transition-colors"
              style={{ color: copied ? "var(--success, var(--accent))" : "var(--text-dim)" }}
            >
              {copied ? <Check size={12} weight="bold" /> : <Copy size={12} />}
              {copied ? "已复制" : "复制"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
/* ------ Tool call item (collapsible) --------------------------------------- */
function ToolCallItem({ tc }: { tc: { name: string; args: string; result: string } }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}
    >
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs"
        style={{ color: "var(--text-muted)" }}
      >
        <Wrench size={12} weight="fill" style={{ color: "var(--accent)" }} />
        <span className="font-medium">{tc.name}</span>
        {open ? <CaretDown size={12} /> : <CaretRight size={12} />}
      </button>
      {open && (
        <div className="px-3 py-2 text-xs flex flex-col gap-2" style={{ borderTop: "1px solid var(--border)" }}>
          <div>
            <span style={{ color: "var(--text-dim)" }}>参数: </span>
            <span className="mono" style={{ color: "var(--text-muted)" }}>
              {tc.args || "{}"}
            </span>
          </div>
          {tc.result && (
            <div>
              <span style={{ color: "var(--text-dim)" }}>结果: </span>
              <span className="mono" style={{ color: "var(--text-muted)" }}>
                {tc.result}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
