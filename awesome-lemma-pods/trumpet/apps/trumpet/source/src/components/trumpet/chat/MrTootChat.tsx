/**
 * MrTootChat — right-side sidebar chat experience for Trumpet.
 *
 * Layout: fixed right sidebar (380px) that slides in from the right.
 * Conversation history lives in a toggleable left panel within the sidebar.
 * Rendered outside <Stage> so position:fixed is always viewport-relative.
 */
import * as React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAssistantController } from 'lemma-sdk/react';
import { client } from '@/lib/client';
import { hasPodId, runtimeConfig } from '@/lib/runtime-config';
import { appConfig } from '@/app-config';
import { tokens } from '@/lib/tokens';
import { isSystemConversation } from '@/lib/system-conversations';

// ── Constants ────────────────────────────────────────────────────────────────

const QUICK_ACTIONS = [
  { label: "What's on today?",    icon: '📅' },
  { label: 'Log a commitment',    icon: '🤝' },
  { label: 'Ping someone',        icon: '📨' },
  { label: 'Find a note',         icon: '🔍' },
];

const SHORTCUTS = [
  "What's overdue?",
  'Add a contact',
  'New note',
  "Who haven't I followed up with?",
];

const SIDEBAR_W   = 400;
const HISTORY_W   = 210;

// ── Theme helpers ─────────────────────────────────────────────────────────────

function getIsDark(): boolean {
  if (typeof document === 'undefined') return true;
  return document.documentElement.classList.contains('dark');
}

function useIsDark(): boolean {
  const [isDark, setIsDark] = React.useState(getIsDark);
  React.useEffect(() => {
    const obs = new MutationObserver(() => setIsDark(getIsDark()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
  }, []);
  return isDark;
}

const ThemeCtx = React.createContext(true);

function mkTheme(isDark: boolean) {
  return {
    panelBg:       isDark ? '#161514'                 : '#ffffff',
    border1:       isDark ? 'rgba(255,255,255,0.07)'  : 'rgba(0,0,0,0.07)',
    border2:       isDark ? 'rgba(255,255,255,0.08)'  : 'rgba(0,0,0,0.08)',
    border3:       isDark ? 'rgba(255,255,255,0.09)'  : 'rgba(0,0,0,0.09)',
    border4:       isDark ? 'rgba(255,255,255,0.10)'  : 'rgba(0,0,0,0.10)',
    border5:       isDark ? 'rgba(255,255,255,0.12)'  : 'rgba(0,0,0,0.12)',
    cardBg1:       isDark ? 'rgba(255,255,255,0.04)'  : 'rgba(0,0,0,0.03)',
    cardBg2:       isDark ? 'rgba(255,255,255,0.05)'  : 'rgba(0,0,0,0.04)',
    cardBg3:       isDark ? 'rgba(255,255,255,0.07)'  : 'rgba(0,0,0,0.05)',
    cardBgHov:     isDark ? 'rgba(255,255,255,0.09)'  : 'rgba(0,0,0,0.06)',
    userBubble:    isDark ? 'rgba(255,255,255,0.11)'  : 'rgba(0,0,0,0.07)',
    muted1:        isDark ? 'rgba(255,255,255,0.20)'  : 'rgba(0,0,0,0.28)',
    muted2:        isDark ? 'rgba(255,255,255,0.22)'  : 'rgba(0,0,0,0.30)',
    muted3:        isDark ? 'rgba(255,255,255,0.25)'  : 'rgba(0,0,0,0.35)',
    muted4:        isDark ? 'rgba(255,255,255,0.30)'  : 'rgba(0,0,0,0.40)',
    muted5:        isDark ? 'rgba(255,255,255,0.35)'  : 'rgba(0,0,0,0.45)',
    muted6:        isDark ? 'rgba(255,255,255,0.40)'  : 'rgba(0,0,0,0.50)',
    cursor:        isDark ? 'rgba(255,255,255,0.70)'  : 'rgba(0,0,0,0.70)',
    codeBg:        isDark ? 'rgba(255,255,255,0.07)'  : 'rgba(0,0,0,0.05)',
    inlineCodeBg:  isDark ? 'rgba(255,255,255,0.10)'  : 'rgba(0,0,0,0.07)',
    inputBg:       isDark ? 'rgba(255,255,255,0.07)'  : 'rgba(0,0,0,0.04)',
    inputBorder:   isDark ? 'rgba(255,255,255,0.12)'  : 'rgba(0,0,0,0.12)',
    iconHovBg:     isDark ? 'rgba(255,255,255,0.09)'  : 'rgba(0,0,0,0.06)',
    thinkingDot:   isDark ? 'rgba(255,255,255,0.35)'  : 'rgba(0,0,0,0.30)',
    sendDisabled:  isDark ? 'rgba(255,255,255,0.07)'  : 'rgba(0,0,0,0.06)',
    sendIconDim:   isDark ? 'rgba(255,255,255,0.30)'  : 'rgba(0,0,0,0.25)',
    chipText:      isDark ? 'rgba(255,255,255,0.40)'  : 'rgba(0,0,0,0.45)',
    chipBg:        isDark ? 'rgba(255,255,255,0.05)'  : 'rgba(0,0,0,0.04)',
  };
}

// ── Types ────────────────────────────────────────────────────────────────────

interface MessagePart {
  type: string;
  text?: string;
  toolInvocation?: { toolName: string; state: string };
}

interface Msg {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  parts?: MessagePart[];
  createdAt?: Date;
}

interface ConvMeta {
  id: string;
  title?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function msgText(msg: Msg): string {
  if (msg.parts && msg.parts.length > 0) {
    const t = msg.parts.filter(p => p.type === 'text').map(p => p.text ?? '').join('');
    if (t) return t;
  }
  return msg.content ?? '';
}

function groupTurns(messages: Msg[]): Array<{ role: 'user' | 'assistant'; messages: Msg[] }> {
  const turns: Array<{ role: 'user' | 'assistant'; messages: Msg[] }> = [];
  for (const msg of messages) {
    if (msg.role === 'system') continue;
    const last = turns[turns.length - 1];
    if (last && last.role === msg.role) {
      last.messages.push(msg);
    } else {
      turns.push({ role: msg.role, messages: [msg] });
    }
  }
  return turns;
}

function relTime(dateStr?: string | null): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)   return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

function isToday(dateStr?: string | null): boolean {
  if (!dateStr) return false;
  const d = new Date(dateStr);
  const now = new Date();
  return d.getDate() === now.getDate() &&
    d.getMonth() === now.getMonth() &&
    d.getFullYear() === now.getFullYear();
}

function convTitle(conv: ConvMeta): string {
  if (conv.title && conv.title.trim() && conv.title !== 'New conversation') return conv.title;
  return 'Conversation';
}

// ── Sub-components ───────────────────────────────────────────────────────────

function MrTootAvatar({ size = 28 }: { size?: number }) {
  const [imgErr, setImgErr] = React.useState(false);
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      background: '#e8d4a8', flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: size * 0.52, overflow: 'hidden',
    }}>
      {!imgErr
        ? <img src="/mascot/trumpet-chill.png" alt="Mr Toot" onError={() => setImgErr(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        : <span>🎺</span>}
    </div>
  );
}

function ToolChip({ name }: { name: string }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const label = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: 11.5, color: th.chipText,
      background: th.chipBg,
      border: `1px solid ${th.border4}`,
      borderRadius: 6, padding: '2px 7px', fontFamily: tokens.font,
    }}>
      <span>⚙</span> {label}
    </span>
  );
}

function ThinkingDots() {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  return (
    <span style={{ display: 'inline-flex', gap: 3, alignItems: 'center', height: 18 }}>
      {[0,1,2].map(i => (
        <span key={i} style={{
          width: 5, height: 5, borderRadius: '50%',
          background: th.thinkingDot,
          animation: `mrtDot 1.2s ${i * 0.2}s ease-in-out infinite`,
        }} />
      ))}
    </span>
  );
}

function AssistantTurn({ messages, isStreaming }: { messages: Msg[]; isStreaming: boolean }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const allTools: string[] = [];
  let fullText = '';

  for (const msg of messages) {
    if (msg.parts) {
      for (const p of msg.parts) {
        if (p.type === 'tool' && p.toolInvocation) allTools.push(p.toolInvocation.toolName);
        if (p.type === 'text' && p.text) fullText += p.text;
      }
    } else {
      fullText += msg.content ?? '';
    }
  }

  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <MrTootAvatar size={26} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {allTools.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: fullText ? 6 : 0 }}>
            {allTools.map((t, i) => <ToolChip key={i} name={t} />)}
          </div>
        )}
        {fullText ? (
          <div style={{ fontSize: 14, color: tokens.fg, fontFamily: tokens.font, lineHeight: 1.65 }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p:    ({ children }) => <p style={{ margin: '0 0 8px' }}>{children}</p>,
                pre:  ({ children }) => <pre style={{ background: th.codeBg, borderRadius: 8, padding: '10px 14px', overflowX: 'auto', fontSize: 12.5, margin: '6px 0' }}>{children}</pre>,
                code: ({ children, className }) =>
                  className
                    ? <code>{children}</code>
                    : <code style={{ background: th.inlineCodeBg, borderRadius: 4, padding: '1px 5px', fontSize: 12.5 }}>{children}</code>,
                ul:   ({ children }) => <ul style={{ paddingLeft: 18, margin: '4px 0' }}>{children}</ul>,
                ol:   ({ children }) => <ol style={{ paddingLeft: 18, margin: '4px 0' }}>{children}</ol>,
                li:   ({ children }) => <li style={{ marginBottom: 3 }}>{children}</li>,
                a:    ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: '#e8d4a8', textDecoration: 'underline' }}>{children}</a>,
              }}
            >
              {fullText}
            </ReactMarkdown>
            {isStreaming && (
              <span style={{ display: 'inline-block', width: 2, height: 14, background: th.cursor, marginLeft: 2, verticalAlign: 'text-bottom', animation: 'mrtBlink 0.8s ease-in-out infinite' }} />
            )}
          </div>
        ) : isStreaming ? (
          <ThinkingDots />
        ) : null}
      </div>
    </div>
  );
}

function UserTurn({ messages }: { messages: Msg[] }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const text = messages.map(m => msgText(m)).join('\n');
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={{
        maxWidth: '78%', padding: '9px 14px',
        background: th.userBubble,
        borderRadius: '18px 18px 4px 18px',
        fontSize: 14, color: tokens.fg, fontFamily: tokens.font,
        lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {text}
      </div>
    </div>
  );
}

// ── Empty state ──────────────────────────────────────────────────────────────

function EmptyState({ onSend }: { onSend: (t: string) => void }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '24px 20px 12px', gap: 18, overflowY: 'auto' }}>
      <div style={{ textAlign: 'center' }}>
        <MrTootAvatar size={52} />
        <p style={{ margin: '10px 0 3px', fontSize: 19, fontWeight: 700, color: tokens.fg, fontFamily: tokens.font, letterSpacing: -0.4 }}>
          How can I help?
        </p>
        <p style={{ margin: 0, fontSize: 13, color: tokens.muted, fontFamily: tokens.font }}>
          Ask me anything or pick a quick action
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 7, width: '100%' }}>
        {QUICK_ACTIONS.map(qa => <QuickCard key={qa.label} {...qa} onSelect={() => onSend(qa.label)} />)}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, justifyContent: 'center' }}>
        {SHORTCUTS.map(s => (
          <ShortcutPill key={s} label={s} onClick={() => onSend(s)} />
        ))}
      </div>
    </div>
  );
}

function QuickCard({ icon, label, onSelect }: { icon: string; label: string; onSelect: () => void }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const [hov, setHov] = React.useState(false);
  return (
    <button onClick={onSelect} onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        textAlign: 'left', padding: '11px 13px',
        background: hov ? th.cardBgHov : th.cardBg1,
        border: `1px solid ${th.border4}`, borderRadius: 11,
        cursor: 'pointer', fontFamily: tokens.font, transition: 'background 0.12s',
      }}>
      <div style={{ fontSize: 18, marginBottom: 5 }}>{icon}</div>
      <div style={{ fontSize: 12.5, fontWeight: 500, color: tokens.fg, lineHeight: 1.4 }}>{label}</div>
    </button>
  );
}

function ShortcutPill({ label, onClick }: { label: string; onClick: () => void }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const [hov, setHov] = React.useState(false);
  return (
    <button onClick={onClick} onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        padding: '4px 11px', fontSize: 12, fontWeight: 500,
        color: hov ? tokens.fg : tokens.muted,
        background: hov ? th.cardBgHov : th.cardBg2,
        border: `1px solid ${th.border4}`, borderRadius: 100,
        cursor: 'pointer', fontFamily: tokens.font, transition: 'all 0.12s',
      }}>
      {label}
    </button>
  );
}

// ── Input bar ────────────────────────────────────────────────────────────────

function InputBar({ onSend, disabled }: { onSend: (t: string) => void; disabled: boolean }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const [val, setVal] = React.useState('');
  const ref = React.useRef<HTMLTextAreaElement>(null);

  const send = () => {
    const t = val.trim();
    if (!t || disabled) return;
    onSend(t);
    setVal('');
    if (ref.current) ref.current.style.height = 'auto';
  };

  const canSend = !disabled && !!val.trim();

  return (
    <div style={{ padding: '10px 12px 8px', borderTop: `1px solid ${th.border2}`, flexShrink: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'flex-end', gap: 8,
        background: th.inputBg, border: `1px solid ${th.inputBorder}`,
        borderRadius: 13, padding: '7px 8px 7px 13px',
      }}>
        <textarea
          ref={ref} value={val}
          onChange={e => {
            setVal(e.target.value);
            if (ref.current) { ref.current.style.height = 'auto'; ref.current.style.height = Math.min(ref.current.scrollHeight, 120) + 'px'; }
          }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Message Mr. Toot…"
          rows={1}
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            resize: 'none', fontSize: 14, color: tokens.fg, fontFamily: tokens.font,
            lineHeight: 1.5, minHeight: 21, maxHeight: 120,
          }}
        />
        <button onClick={send} disabled={!canSend}
          style={{
            width: 30, height: 30, borderRadius: '50%', flexShrink: 0, border: 'none',
            background: canSend ? '#e8d4a8' : th.sendDisabled,
            cursor: canSend ? 'pointer' : 'default', display: 'flex',
            alignItems: 'center', justifyContent: 'center', transition: 'background 0.15s',
          }}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <path d="M6.5 11V2M6.5 2L3 5.5M6.5 2L10 5.5"
              stroke={canSend ? '#1a1008' : th.sendIconDim}
              strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <p style={{ textAlign: 'center', fontSize: 10.5, color: th.muted1, margin: '5px 0 0', fontFamily: tokens.font }}>
        Mr. Toot can make mistakes. Check important info.
      </p>
    </div>
  );
}

// ── Conversation history panel ────────────────────────────────────────────────

function HistoryPanel({
  conversations, activeId, onSelect, onNew,
}: {
  conversations: ConvMeta[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const human = conversations.filter(c => !isSystemConversation(c.id));
  const todayConvs   = human.filter(c => isToday(c.updated_at ?? c.created_at));
  const earlierConvs = human.filter(c => !isToday(c.updated_at ?? c.created_at));

  const Item = ({ c }: { c: ConvMeta }) => {
    const [hov, setHov] = React.useState(false);
    const active = c.id === activeId;
    return (
      <button
        onClick={() => onSelect(c.id)}
        onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
        style={{
          width: '100%', textAlign: 'left', padding: '7px 10px',
          background: active ? 'rgba(232,212,168,0.1)' : hov ? th.cardBg2 : 'transparent',
          border: active ? '1px solid rgba(232,212,168,0.2)' : '1px solid transparent',
          borderRadius: 8, cursor: 'pointer', transition: 'all 0.1s',
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 500, color: tokens.fg, fontFamily: tokens.font, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {convTitle(c)}
        </div>
        <div style={{ fontSize: 10.5, color: tokens.muted, fontFamily: tokens.font, marginTop: 1 }}>
          {relTime(c.updated_at ?? c.created_at)}
        </div>
      </button>
    );
  };

  const SectionLabel = ({ label }: { label: string }) => (
    <div style={{ fontSize: 10, fontWeight: 700, color: th.muted3, letterSpacing: '0.08em', textTransform: 'uppercase', padding: '8px 10px 3px', fontFamily: tokens.font }}>
      {label}
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', borderRight: `1px solid ${th.border1}` }}>
      <div style={{ padding: '14px 10px 10px', borderBottom: `1px solid ${th.border1}`, flexShrink: 0 }}>
        <p style={{ margin: '0 0 8px 2px', fontSize: 11, fontWeight: 700, color: th.muted4, letterSpacing: '0.07em', textTransform: 'uppercase', fontFamily: tokens.font }}>
          Chats
        </p>
        <button onClick={onNew}
          style={{
            width: '100%', padding: '6px 10px',
            background: 'rgba(232,212,168,0.08)', border: '1px solid rgba(232,212,168,0.18)',
            borderRadius: 8, cursor: 'pointer', fontSize: 12,
            fontWeight: 600, color: '#e8d4a8', fontFamily: tokens.font,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
          <span style={{ fontSize: 13 }}>✎</span> New chat
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 6px' }}>
        {human.length === 0 ? (
          <p style={{ fontSize: 11.5, color: th.muted2, textAlign: 'center', padding: '20px 8px', fontFamily: tokens.font }}>
            No past chats yet
          </p>
        ) : (
          <>
            {todayConvs.length > 0 && (
              <>
                <SectionLabel label="Today" />
                {todayConvs.map(c => <Item key={c.id} c={c} />)}
              </>
            )}
            {earlierConvs.length > 0 && (
              <>
                <SectionLabel label="Earlier" />
                {earlierConvs.map(c => <Item key={c.id} c={c} />)}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── CSS ──────────────────────────────────────────────────────────────────────

const CSS = `
@keyframes mrtDot {
  0%,80%,100% { transform:scale(0.55); opacity:0.35; }
  40%          { transform:scale(1);    opacity:1; }
}
@keyframes mrtBlink {
  0%,100% { opacity:1; }
  50%      { opacity:0; }
}
@keyframes mrtSlideIn {
  from { opacity:0; transform:translateX(24px); }
  to   { opacity:1; transform:translateX(0); }
}
@keyframes mrtHistoryIn {
  from { opacity:0; transform:translateX(-10px); }
  to   { opacity:1; transform:translateX(0); }
}
`;

function IconBtn({ onClick, title, children, active }: { onClick: () => void; title: string; children: React.ReactNode; active?: boolean }) {
  const isDark = React.useContext(ThemeCtx);
  const th = mkTheme(isDark);
  const [hov, setHov] = React.useState(false);
  return (
    <button onClick={onClick} title={title}
      onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        background: active ? 'rgba(232,212,168,0.12)' : hov ? th.iconHovBg : 'none',
        border: 'none', cursor: 'pointer',
        color: active ? '#e8d4a8' : hov ? tokens.fg : th.muted6,
        padding: '5px 7px', borderRadius: 6, fontSize: 14,
        lineHeight: 1, transition: 'all 0.12s', fontFamily: tokens.font,
      }}>
      {children}
    </button>
  );
}

// ── Trigger button ───────────────────────────────────────────────────────────

function TriggerButton({ onClick }: { onClick: () => void }) {
  const [hov, setHov] = React.useState(false);
  const [imgErr, setImgErr] = React.useState(false);
  return (
    <button onClick={onClick}
      onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        display: 'flex', alignItems: 'center', gap: 9,
        padding: '9px 18px 9px 11px', border: 'none', borderRadius: 100,
        background: hov ? '#d9c490' : '#e8d4a8',
        boxShadow: hov
          ? '0 8px 28px -6px rgba(0,0,0,0.4), 0 2px 8px -2px rgba(0,0,0,0.2)'
          : '0 4px 18px -4px rgba(0,0,0,0.3)',
        transform: hov ? 'translateY(-2px)' : 'none',
        transition: 'all 0.18s ease', cursor: 'pointer',
        fontFamily: tokens.font,
      }}>
      <div style={{ width: 34, height: 34, borderRadius: '50%', background: 'rgba(0,0,0,0.12)', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0 }}>
        {!imgErr
          ? <img src="/mascot/trumpet-chill.png" alt="" onError={() => setImgErr(true)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          : <span>🎺</span>}
      </div>
      <span style={{ fontSize: 13, fontWeight: 700, color: '#1a1008', whiteSpace: 'nowrap', letterSpacing: -0.1 }}>
        get chatty with Mr. Toot
      </span>
    </button>
  );
}

// ── Main export ──────────────────────────────────────────────────────────────

export function MrTootChat() {
  const isDark = useIsDark();
  const th = mkTheme(isDark);

  const [open,        setOpen]        = React.useState(false);
  const [showHistory, setShowHistory] = React.useState(true);

  const agentName = runtimeConfig.agentName || appConfig.agent?.agentName || undefined;

  const ctrl = useAssistantController({
    client,
    podId:     runtimeConfig.podId || undefined,
    agentName,
    enabled:   hasPodId && Boolean(agentName),
  });

  React.useEffect(() => {
    ctrl.selectConversation(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) { setOpen(false); }
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setOpen(o => !o); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open]);

  const handleSend = async (text: string) => {
    await ctrl.sendMessage(text);
  };

  const handleNew = () => {
    ctrl.selectConversation(null);
  };

  const turns  = groupTurns(ctrl.messages as Msg[]);
  const isEmpty = turns.length === 0;
  const endRef  = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [ctrl.messages.length, ctrl.isActiveConversationRunning]);

  const totalW = showHistory ? SIDEBAR_W + HISTORY_W : SIDEBAR_W;

  return (
    <ThemeCtx.Provider value={isDark}>
      <style>{CSS}</style>

      {/* Trigger — always visible bottom-right, viewport-fixed */}
      <div style={{ position: 'fixed', bottom: 28, right: 28, zIndex: 200 }}>
        <TriggerButton onClick={() => setOpen(o => !o)} />
      </div>

      {/* Backdrop */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 299, background: 'rgba(0,0,0,0.35)' }}
        />
      )}

      {/* Sidebar panel */}
      {open && (
        <div style={{
          position: 'fixed', top: 0, right: 0, bottom: 0,
          width: totalW,
          background: th.panelBg,
          borderLeft: `1px solid ${th.border3}`,
          boxShadow: '-12px 0 40px -8px rgba(0,0,0,0.6)',
          zIndex: 300,
          display: 'flex',
          flexDirection: 'row',
          animation: 'mrtSlideIn 0.22s ease-out',
          overflow: 'hidden',
        }}>

          {/* History panel */}
          {showHistory && (
            <div style={{
              width: HISTORY_W, flexShrink: 0,
              animation: 'mrtHistoryIn 0.18s ease-out',
            }}>
              <HistoryPanel
                conversations={ctrl.conversations as ConvMeta[]}
                activeId={ctrl.activeConversationId}
                onSelect={ctrl.selectConversation}
                onNew={handleNew}
              />
            </div>
          )}

          {/* Main chat column */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

            {/* Header */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '14px 14px 12px',
              borderBottom: `1px solid ${th.border2}`, flexShrink: 0,
            }}>
              <IconBtn title="Toggle history" onClick={() => setShowHistory(h => !h)} active={showHistory}>
                ☰
              </IconBtn>
              <MrTootAvatar size={30} />
              <div style={{ flex: 1 }}>
                <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: tokens.fg, fontFamily: tokens.font, letterSpacing: -0.3 }}>
                  Mr. Toot
                </p>
                <p style={{ margin: 0, fontSize: 11, fontFamily: tokens.font, color: ctrl.isActiveConversationRunning ? '#4ade80' : th.muted5, transition: 'color 0.3s' }}>
                  {ctrl.isActiveConversationRunning ? 'typing…' : 'Your personal assistant'}
                </p>
              </div>
              <div style={{ display: 'flex', gap: 2 }}>
                <IconBtn title="New conversation" onClick={handleNew}>✎</IconBtn>
                <IconBtn title="Close" onClick={() => setOpen(false)}>✕</IconBtn>
              </div>
            </div>

            {/* Error banner */}
            {ctrl.error && (
              <div style={{ padding: '7px 14px', background: 'rgba(239,68,68,0.12)', borderBottom: '1px solid rgba(239,68,68,0.2)', flexShrink: 0 }}>
                <p style={{ margin: 0, fontSize: 12, color: '#fca5a5', fontFamily: tokens.font }}>{ctrl.error}</p>
              </div>
            )}

            {/* Messages / empty state */}
            {isEmpty ? (
              <EmptyState onSend={handleSend} />
            ) : (
              <div
                style={{ flex: 1, overflowY: 'auto', padding: '16px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}
                onWheel={e => e.stopPropagation()}
              >
                {turns.map((turn, i) => {
                  const isLast = i === turns.length - 1;
                  const streaming = isLast && turn.role === 'assistant' && ctrl.isActiveConversationRunning;
                  return turn.role === 'assistant'
                    ? <AssistantTurn key={i} messages={turn.messages} isStreaming={streaming} />
                    : <UserTurn key={i} messages={turn.messages} />;
                })}
                <div ref={endRef} />
              </div>
            )}

            <InputBar onSend={handleSend} disabled={ctrl.isActiveConversationRunning} />
          </div>
        </div>
      )}
    </ThemeCtx.Provider>
  );
}
