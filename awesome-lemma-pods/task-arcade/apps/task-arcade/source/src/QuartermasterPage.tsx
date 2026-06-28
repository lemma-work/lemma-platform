import { ArrowLeft, Calendar, Clock, Inbox, List, Send, Sparkles, UserPlus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useConversationMessages, useRecords } from "lemma-sdk/react";
import { client, gravatarUrl } from "./lemma";
import type { AgentActionRow, TaskRow } from "./lemma";

const EMAIL_COLORS: Record<string, string> = {
  "manager@example.com": "#2f8d4d",
  "asha@example.com": "#42be65",
  "rohan@example.com": "#4f90df",
  "maya@example.com": "#a878e4",
  "kabir@example.com": "#efad32",
  "neha@example.com": "#e9627a",
};
const FALLBACK_COLORS = ["#5bb0a6", "#d98a5b", "#c75b7a", "#7c9a3e", "#3f9ec0"];

function colorFor(email: string): string {
  if (EMAIL_COLORS[email]) return EMAIL_COLORS[email];
  let h = 0;
  for (let i = 0; i < email.length; i++) h = (h * 31 + email.charCodeAt(i)) >>> 0;
  return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
}

function nameFor(email: string): string {
  if (EMAIL_COLORS[email]) {
    const map: Record<string, string> = {
      "manager@example.com": "Manager",
      "asha@example.com": "Asha",
      "rohan@example.com": "Rohan",
      "maya@example.com": "Maya",
      "kabir@example.com": "Kabir",
      "neha@example.com": "Neha",
    };
    return map[email] ?? email.split("@")[0];
  }
  return email.split("@")[0];
}

function timeAgo(iso?: string): string {
  if (!iso) return "just now";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

const KIND_LABEL: Record<string, string> = {
  nudge: "Nudge", standup: "Standup", recap: "Recap",
  hype: "Hype", assign: "Assigned", remind: "Reminder", query: "Query",
};
const KIND_COLOR: Record<string, string> = {
  standup: "#3fa3df", recap: "#8e6fd6", hype: "#efad32",
  nudge: "#f0a92e", assign: "#2f8d4d", remind: "#e9627a", query: "#66806d",
};

type TLEvent = {
  id: string; type: string; actor: string; actorEmail: string; subject: string;
  pts: number; at: string; color: string;
};

function buildTimeline(taskRecs: TaskRow[]): TLEvent[] {
  const evts: TLEvent[] = [];
  taskRecs.forEach((t) => {
    const actorEmail = t.assignee;
    const actorName = nameFor(actorEmail);
    const status = t.status;
    const pts = t.points;

    evts.push({ id: `ta-${t.id}`, type: "assigned", actor: actorName, actorEmail, subject: t.title, pts, at: t.created_at ?? "", color: "#3fa3df" });

    if (status === "cleared" || status === "under_review" || status === "established" || status === "demolished") {
      evts.push({ id: `tc-${t.id}`, type: "cleared", actor: actorName, actorEmail, subject: t.title, pts, at: t.updated_at ?? t.created_at ?? "", color: "#2f8d4d" });
    }
    if (status === "under_review" || status === "established" || status === "demolished") {
      evts.push({ id: `tp-${t.id}`, type: "placed", actor: actorName, actorEmail, subject: t.title, pts, at: t.updated_at ?? t.created_at ?? "", color: "#efad32" });
    }
    if (status === "established") {
      const reviewerEmail = t.reviewer ?? "";
      const reviewerName = t.reviewer ? nameFor(t.reviewer) : "Manager";
      evts.push({ id: `te-${t.id}`, type: "established", actor: reviewerName, actorEmail: reviewerEmail, subject: t.title, pts, at: t.updated_at ?? t.created_at ?? "", color: "#8e6fd6" });
    }
    if (status === "demolished") {
      const reviewerEmail = t.reviewer ?? "";
      const reviewerName = t.reviewer ? nameFor(t.reviewer) : "Manager";
      evts.push({ id: `tx-${t.id}`, type: "demolished", actor: reviewerName, actorEmail: reviewerEmail, subject: t.title, pts, at: t.updated_at ?? t.created_at ?? "", color: "#e9627a" });
    }
  });
  evts.sort((a, b) => {
    const ta = a.at ? new Date(a.at).getTime() : 0;
    const tb = b.at ? new Date(b.at).getTime() : 0;
    return tb - ta;
  });
  return evts;
}

type MainTab = "dispatches" | "timeline";

const ACTION_CHIPS = [
  { icon: "list", label: "What's pending my approval?" },
  { icon: "userplus", label: "Assign Maya a task" },
  { icon: "calendar", label: "Run today's standup" },
];

function ChipIcon({ name }: { name: string }) {
  if (name === "list") return <List size={16} />;
  if (name === "userplus") return <UserPlus size={16} />;
  return <Calendar size={16} />;
}

export function QuartermasterPage() {
  const [mainTab, setMainTab] = useState<MainTab>("dispatches");
  const [input, setInput] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  const { records: actionRecords } = useRecords<AgentActionRow>({
    client, tableName: "agent_actions",
    sort: [{ field: "created_at", direction: "desc" }], limit: 50,
  });
  const { records: taskRecords } = useRecords<TaskRow>({
    client, tableName: "tasks",
    sort: [{ field: "created_at", direction: "desc" }], limit: 100,
  });

  const {
    messages, sendMessage, isRunning, streamingText, conversationId,
    createConversation, isLoading,
  } = useConversationMessages({
    client, agentName: "quartermaster", autoLoad: true,
  });

  const initiatedRef = useRef(false);
  useEffect(() => {
    if (initiatedRef.current || conversationId) return;
    initiatedRef.current = true;
    createConversation().catch(() => { initiatedRef.current = false; });
  }, [conversationId, createConversation]);

  const visibleMessages = useMemo(
    () => messages.filter(
      (m) => m.role === "user" || (m.role === "assistant" && m.kind === "text"),
    ),
    [messages],
  );

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [visibleMessages, isRunning, streamingText]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || isRunning) return;
    setInput("");
    try { await sendMessage(text); } catch { setInput(text); }
  }, [input, isRunning, sendMessage]);

  const sendPreset = useCallback((preset: string) => {
    if (isRunning) return;
    sendMessage(preset).catch(() => {});
  }, [isRunning, sendMessage]);

  const timeline = useMemo(
    () => buildTimeline(taskRecords),
    [taskRecords],
  );

  const showEmpty = visibleMessages.length === 0 && !isLoading && !isRunning;

  return (
    <div className="qm-page">
      {/* ── Top header — thin ── */}
      <header className="qm-top-bar">
        <a href="#" className="qm-back-link">
          <ArrowLeft size={16} /> Back to world
        </a>
        <div className="qm-top-title">
          <Sparkles size={15} />
          <span className="qm-top-name">Quartermaster</span>
          <span className="qm-top-sub">The agent that runs the board</span>
        </div>
        <span className="qm-live-badge">
          <span className="qm-live-dot" /> LIVE
        </span>
      </header>

      {/* ── Body: main area + chat sidebar ── */}
      <div className="qm-body">
        {/* Left/main workspace */}
        <main className="qm-main">
          <nav className="qm-main-tabs">
            {(["dispatches", "timeline"] as MainTab[]).map((tab) => (
              <button
                key={tab}
                className={`qm-main-tab${mainTab === tab ? " is-active" : ""}`}
                onClick={() => setMainTab(tab)}
                type="button"
              >
                {tab === "dispatches" ? "Dispatches" : "Timeline"}
              </button>
            ))}
          </nav>

          <div className="qm-main-content">
            {mainTab === "dispatches" && (
              <div className="qm-dispatches-area">
                <div className="qm-main-intro">
                  <h2>Dispatches</h2>
                  <p>Updates, approvals, and board activity from Quartermaster.</p>
                </div>
                {actionRecords.length === 0 ? (
                  <div className="qm-empty-card">
                    <div className="qm-empty-icon"><Inbox size={22} /></div>
                    <strong>No dispatches yet</strong>
                    <span>When Quartermaster assigns, escalates, or recaps something, it will appear here.</span>
                  </div>
                ) : (
                  <div className="qm-dispatch-list">
                    {actionRecords.map((d) => {
                      const kind = d.kind as string;
                      const kindColor = KIND_COLOR[kind] ?? "#66806d";
                      return (
                        <article key={d.id} className="dispatch-card">
                          <div className="dispatch-head">
                            <span className="dispatch-kind" style={{ background: kindColor + "22", color: kindColor }}>
                              {KIND_LABEL[kind] ?? kind}
                            </span>
                            <span className="dispatch-time"><Clock size={11} /> {timeAgo(d.created_at)}</span>
                          </div>
                          <pre className="dispatch-body">{d.result ?? d.payload ?? "—"}</pre>
                        </article>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {mainTab === "timeline" && (
              <div className="qm-timeline-area">
                <div className="qm-main-intro">
                  <h2>Timeline</h2>
                  <p>Every assignment, clearance, placement, and review — newest first.</p>
                </div>
                {timeline.length === 0 ? (
                  <div className="qm-empty-card">
                    <div className="qm-empty-icon"><Inbox size={22} /></div>
                    <strong>No activity yet</strong>
                    <span>Assign a task to see the timeline.</span>
                  </div>
                ) : (
                  <div className="qm-timeline-list">
                    {timeline.map((evt, i) => (
                      <div key={evt.id} className="tl-event">
                        <div className="tl-track">
                          <span className="tl-dot" style={{ background: evt.color }} />
                          {i < timeline.length - 1 && <span className="tl-line" />}
                        </div>
                        <div className="tl-body">
                          <div className="tl-top">
                            <span className="qm-tl-av" style={{ background: colorFor(evt.actorEmail) }}>
                              {(evt.actor.trim()[0] ?? "?").toUpperCase()}
                            </span>
                            <span className="tl-actor">{evt.actor}</span>
                            <span className="tl-verb">{evt.type}</span>
                            {evt.pts > 0 && (
                              <span className="tl-pts" style={{ background: "#66806d22", color: "#66806d" }}>
                                {evt.pts}
                              </span>
                            )}
                          </div>
                          <p className="tl-subject">{evt.subject}</p>
                          {evt.at && <span className="tl-time">{timeAgo(evt.at)}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </main>

        {/* Right: permanent chat sidebar */}
        <aside className="qm-chat-sidebar">
          <div className="qm-chat-header">
            <div className="qm-chat-title-row">
              <span className="qm-chat-icon"><Sparkles size={14} /></span>
              <strong>Command Post</strong>
              <span className="qm-chat-live"><span className="qm-live-dot" /> LIVE</span>
            </div>
            <p className="qm-chat-subtitle">Quartermaster is already in your channel.</p>
          </div>

          {showEmpty && (
            <div className="qm-chat-welcome">
              <div className="qm-welcome-msg">
                <span className="qm-welcome-av"><Sparkles size={13} /></span>
                <div className="qm-welcome-bubble">
                  <span className="qm-wave">👋</span> I'm Quartermaster. How can I help your team today?
                </div>
              </div>
              <div className="qm-action-chips">
                {ACTION_CHIPS.map((chip) => (
                  <button
                    key={chip.label}
                    className="qm-action-chip"
                    onClick={() => sendPreset(chip.label)}
                    disabled={isRunning}
                    type="button"
                  >
                    <span className="qm-chip-icon"><ChipIcon name={chip.icon} /></span>
                    {chip.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="qm-chat-messages">
            {visibleMessages.map((msg) => (
              <div key={msg.id} className={`chat-msg chat-msg--${msg.role === "user" ? "me" : "qm"}`}>
                {msg.role === "assistant" && (
                  <span className="chat-qm-av"><Sparkles size={11} /></span>
                )}
                <div className="chat-bubble">
                  {(msg.text ?? "").split("\n").map((line, i, arr) => (
                    <span key={i}>
                      {line}
                      {i < arr.length - 1 && <br />}
                    </span>
                  ))}
                  <span className="chat-time">
                    {msg.created_at ? timeAgo(msg.created_at) : ""}
                  </span>
                </div>
              </div>
            ))}

            {isRunning && streamingText && (
              <div className="chat-msg chat-msg--qm">
                <span className="chat-qm-av"><Sparkles size={11} /></span>
                <div className="chat-bubble">
                  {streamingText.split("\n").map((line, i, arr) => (
                    <span key={i}>
                      {line}
                      {i < arr.length - 1 && <br />}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {isRunning && !streamingText && (
              <div className="chat-msg chat-msg--qm">
                <span className="chat-qm-av"><Sparkles size={11} /></span>
                <div className="chat-bubble chat-bubble--typing">
                  <span /><span /><span />
                </div>
              </div>
            )}

            <div ref={chatEndRef} />
          </div>

          <div className="qm-chat-input-row">
            <input
              className="qm-chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Ask Quartermaster to assign, recap, or inspect…"
              disabled={isRunning}
            />
            <button
              className="qm-chat-send"
              type="button"
              onClick={send}
              disabled={!input.trim() || isRunning}
              aria-label="Send"
            >
              <Send size={16} />
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
