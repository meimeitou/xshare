"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  streamChat,
  getSessions,
  getHistory,
  deleteSession as deleteSessionApi,
  type ChatEvent,
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
          setMessages(history.map(historyToMessage));
        } else {
          startNewSession();
        }
      } catch {
        startNewSession();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* auto-scroll on new messages */
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

  const startNewSession = useCallback(() => {
    const sid = crypto.randomUUID();
    setSessions((prev) => [
      { session_id: sid, title: "新对话", message_count: 0 },
      ...prev,
    ]);
    setActiveSession(sid);
    setMessages([]);
  }, []);

  const switchSession = useCallback(
    async (sid: string) => {
      if (loading) return;
      setSidebarOpen(false);
      setActiveSession(sid);
      try {
        const history = await getHistory(sid);
        setMessages(history.map(historyToMessage));
      } catch {
        setMessages([]);
      }
    },
    [loading],
  );

  const deleteSession = useCallback(
    async (sid: string) => {
      if (loading) return;
      // Call backend to delete session state
      try { await deleteSessionApi(sid); } catch { /* non-critical */ }
      setSessions((prev) => prev.filter((s) => s.session_id !== sid));
      if (activeSession === sid) {
        const remaining = sessions.filter((s) => s.session_id !== sid);
        if (remaining.length > 0) {
          const nextSid = remaining[0].session_id;
          setActiveSession(nextSid);
          try {
            const history = await getHistory(nextSid);
            setMessages(history.map(historyToMessage));
          } catch {
            setMessages([]);
          }
        } else {
          startNewSession();
        }
      }
    },
    [loading, activeSession, sessions, startNewSession],
  );

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading || !activeSession) return;

      setInput("");
      setLoading(true);

      const userMsg: Message = { role: "user", content: trimmed };
      const assistantMsg: Message = { role: "assistant", content: "", streaming: true };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      // Update session title if it was "新对话"
      setSessions((prev) =>
        prev.map((s) =>
          s.session_id === activeSession && s.title === "新对话"
            ? { ...s, title: trimmed.slice(0, 40), message_count: s.message_count + 2 }
            : s,
        ),
      );

      const ac = new AbortController();
      abortRef.current = ac;

      try {
        for await (const ev of streamChat(activeSession, trimmed, ac.signal)) {
          setMessages((prev) => {
            const next = [...prev];
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
            return next;
          });
        }
        // Refresh session list to get updated counts
        try {
          const refreshed = await getSessions();
          setSessions(refreshed);
        } catch { /* non-critical */ }
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              next[next.length - 1] = { ...last, streaming: false, content: last.content + "\n\n[已取消]" };
            }
            return next;
          });
        } else {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                content: last.content + "\n\n[错误] " + (e as Error).message,
                streaming: false,
              };
            }
            return next;
          });
        }
      } finally {
        setLoading(false);
        abortRef.current = null;
      }
    },
    [loading, activeSession],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  const cancel = () => {
    abortRef.current?.abort();
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
        loading={loading}
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
              disabled={loading}
              className="flex items-center gap-1 px-3 py-1.5 text-xs"
              style={{
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border)",
                background: "var(--bg-elevated)",
                color: "var(--text-muted)",
                opacity: loading ? 0.5 : 1,
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
  loading,
  onNew,
  onSwitch,
  onDelete,
  onClose,
}: {
  sessions: ChatSession[];
  activeSession: string;
  open: boolean;
  loading: boolean;
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
            disabled={loading}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium transition-colors"
            style={{
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--accent)",
              background: "var(--accent-soft)",
              color: "var(--accent-strong)",
              opacity: loading ? 0.5 : 1,
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
                  <span
                    className="text-sm truncate"
                    style={{ fontWeight: active ? 600 : 400 }}
                  >
                    {s.title || "(空对话)"}
                  </span>
                  <span className="text-xs" style={{ color: "var(--text-dim)" }}>
                    {s.message_count} 条消息
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
