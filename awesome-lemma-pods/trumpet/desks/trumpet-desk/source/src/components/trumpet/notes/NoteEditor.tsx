/**
 * NoteEditor — full-screen note editing overlay.
 * Slides in from the right over the NotesTab.
 *
 * A calm, Notion-like writing surface. The note body is the hero; the left
 * sidebar is a lightweight *context* panel (where the thought came from, what
 * it means, what to do with it next) rather than a property/admin inspector.
 *
 * Data-safety rules:
 *  • Body/title/etc are mirrored into refs so closure-captured callbacks
 *    always see the latest value (no stale-closure data loss).
 *  • Auto-saves 800 ms after the user stops typing (debounce).
 *  • Closing always awaits a final save before unmounting.
 *  • onUnmount flushes any pending debounce immediately.
 *
 * The fixed table schema only has title/summary/keywords/category. Extra,
 * editor-only state (pinned, color, body, stage, source) is round-tripped as
 * JSON in the `notes` column.
 */
import * as React from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'framer-motion';
import { client } from '@/lib/client';
import { TABLES } from '@/lib/resources';
import { relativeTime, CATEGORY_CONFIG } from '@/hooks/useNotes';
import type { NoteRecord, NoteCategory } from '@/hooks/useNotes';

// ─── Light, Notion-inspired palette (this overlay is always light) ─────────────

const C = {
  appBg:      '#ffffff',
  sidebarBg:  '#fbfbfa',
  card:       '#ffffff',
  border:     '#ebebea',
  borderSoft: '#f0f0ef',
  text:       '#37352f',
  textSoft:   '#605c54',
  muted:      '#9b9a97',
  faint:      '#b9b8b4',
  chipBg:     '#f1f1ef',
  chipBorder: '#e6e6e4',
  hover:      '#f6f6f5',
  saveBg:     '#1f1d1a',
  saveFg:     '#ffffff',
  green:      '#3a8a4d',
};

const FONT =
  "'Hanken Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif";
const EMOJI_FONT = "'Apple Color Emoji','Segoe UI Emoji',sans-serif";

// ─── Note stage (workflow state) — replaces the old "Pinned" field ─────────────

type Stage = 'inbox' | 'drafting' | 'ready' | 'actioned' | 'archived';

const STAGES: { key: Stage; label: string; dot: string }[] = [
  { key: 'inbox',    label: 'Inbox',    dot: '#9b9a97' },
  { key: 'drafting', label: 'Drafting', dot: '#8a6df0' },
  { key: 'ready',    label: 'Ready',    dot: '#2f9e6f' },
  { key: 'actioned', label: 'Actioned', dot: '#3b82c4' },
  { key: 'archived', label: 'Archived', dot: '#b9b8b4' },
];

function stageMeta(s: Stage) {
  return STAGES.find(x => x.key === s) ?? STAGES[1];
}

// ─── Source channel config ─────────────────────────────────────────────────────

interface NoteSource {
  channel: string;   // 'whatsapp' | 'telegram' | 'gmail' | 'slack' | ...
  who?:    string;   // 'Me', sender name
  at?:     string;   // ISO or human time
  preview?: string;  // original message snippet
  url?:    string;   // link to original
}

const SOURCE_CONFIG: Record<string, { label: string; bg: string; glyph: string }> = {
  whatsapp: { label: 'WhatsApp', bg: '#25d366', glyph: '🟢' },
  telegram: { label: 'Telegram', bg: '#2aabee', glyph: '✈️' },
  gmail:    { label: 'Gmail',    bg: '#ea4335', glyph: '✉️' },
  slack:    { label: 'Slack',    bg: '#611f69', glyph: '#' },
  Trumpet:      { label: 'Trumpet',      bg: '#1f1d1a', glyph: '✎' },
};

function sourceConfig(channel: string) {
  return SOURCE_CONFIG[channel?.toLowerCase?.()] ?? SOURCE_CONFIG.Trumpet;
}

// ─── Meta (de)serialization ────────────────────────────────────────────────────

interface NoteMeta {
  pinned: boolean;
  color:  string;
  body:   string;
  stage:  Stage;
  source: NoteSource | null;
}

function parseMeta(notesJson: string | undefined | null): NoteMeta {
  let obj: Record<string, unknown> = {};
  try { obj = JSON.parse(notesJson ?? '{}') ?? {}; } catch { /* ignore */ }
  const stage = (obj.stage as Stage);
  return {
    pinned: !!obj.pinned,
    color:  (obj.color as string) ?? '',
    body:   (obj.body as string)  ?? '',
    stage:  STAGES.some(s => s.key === stage) ? stage : 'drafting',
    source: (obj.source as NoteSource) ?? null,
  };
}

// keywords <-> chips
function toChips(raw: string): string[] {
  return raw.split(',').map(s => s.trim()).filter(Boolean);
}
function fromChips(chips: string[]): string {
  return chips.join(', ');
}

// AI read — short derived bullets from the summary (read-only, max 3)
function aiBullets(summary: string): string[] {
  return summary
    .split(/(?<=[.!?])\s+|\n+/)
    .map(s => s.trim())
    .filter(Boolean)
    .slice(0, 3);
}

// First N sentences of the body — used as the card-preview summary.
function firstSentences(text: string, n: number): string {
  const parts = text.split(/(?<=[.!?])\s+|\n+/).map(s => s.trim()).filter(Boolean);
  return parts.slice(0, n).join(' ').slice(0, 480);
}

// Textarea that grows to fit its content (so the title wraps instead of
// clipping, and the body pushes the page rather than scrolling internally).
const AutoGrowTextarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function AutoGrowTextarea(props, _ref) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  const resize = React.useCallback(() => {
    const el = ref.current;
    if (el) { el.style.height = 'auto'; el.style.height = `${el.scrollHeight}px`; }
  }, []);
  React.useLayoutEffect(resize, [props.value, resize]);
  return (
    <textarea
      ref={ref}
      rows={1}
      {...props}
      onChange={e => { resize(); props.onChange?.(e); }}
      style={{ ...props.style, resize: 'none', overflow: 'hidden' }}
    />
  );
});

// ─── Small UI atoms ─────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize:      12,
      fontWeight:    600,
      letterSpacing: 0.6,
      textTransform: 'uppercase',
      color:         C.textSoft,
      marginBottom:  11,
    }}>
      {children}
    </div>
  );
}

function IconButton({ title, onClick, children }: {
  title: string; onClick?: () => void; children: React.ReactNode;
}) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      title={title}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
        width:          32,
        height:         32,
        borderRadius:   8,
        background:     hover ? C.hover : 'transparent',
        border:         'none',
        cursor:         'pointer',
        color:          C.textSoft,
        transition:     'background 0.12s',
      }}
    >
      {children}
    </button>
  );
}

// ─── Collection (category) dropdown — center of the top bar ────────────────────

function CollectionDropdown({ value, onChange }: {
  value: NoteCategory; onChange: (c: NoteCategory) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const cfg = CATEGORY_CONFIG[value];

  React.useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display:      'flex',
          alignItems:   'center',
          gap:          8,
          padding:      '6px 12px',
          borderRadius: 8,
          background:   open ? C.hover : 'transparent',
          border:       'none',
          cursor:       'pointer',
          fontSize:     14.5,
          fontWeight:   600,
          color:        C.text,
          fontFamily:   FONT,
          transition:   'background 0.12s',
        }}
      >
        <span style={{ fontFamily: EMOJI_FONT, fontSize: 14 }}>{cfg.emoji}</span>
        {cfg.label}
        <span style={{ fontSize: 11, color: C.muted }}>▾</span>
      </button>

      {open && (
        <div style={{
          position:     'absolute',
          top:          '120%',
          left:         '50%',
          transform:    'translateX(-50%)',
          background:   C.card,
          border:       `1px solid ${C.border}`,
          borderRadius: 12,
          padding:      6,
          boxShadow:    '0 12px 32px -10px rgba(15,15,15,0.18)',
          zIndex:       50,
          minWidth:     200,
        }}>
          {(Object.keys(CATEGORY_CONFIG) as NoteCategory[]).map(cat => {
            const c = CATEGORY_CONFIG[cat];
            const active = cat === value;
            return (
              <button
                key={cat}
                onClick={() => { onChange(cat); setOpen(false); }}
                style={{
                  display:    'flex',
                  alignItems: 'center',
                  gap:        10,
                  width:      '100%',
                  padding:    '8px 10px',
                  borderRadius: 8,
                  background: active ? C.chipBg : 'transparent',
                  border:     'none',
                  cursor:     'pointer',
                  fontSize:   14,
                  fontWeight: active ? 600 : 500,
                  color:      C.text,
                  fontFamily: FONT,
                  textAlign:  'left',
                }}
                onMouseEnter={e => { if (!active) e.currentTarget.style.background = C.hover; }}
                onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
              >
                <span style={{ fontFamily: EMOJI_FONT, fontSize: 15 }}>{c.emoji}</span>
                {c.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Stage dropdown (sidebar) ──────────────────────────────────────────────────

function StageRow({ value, onChange }: { value: Stage; onChange: (s: Stage) => void }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const meta = stageMeta(value);

  React.useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'space-between',
          width:          '100%',
          padding:        '9px 12px',
          borderRadius:   9,
          background:     C.card,
          border:         `1px solid ${C.border}`,
          cursor:         'pointer',
          fontFamily:     FONT,
          transition:     'background 0.12s',
        }}
        onMouseEnter={e => (e.currentTarget.style.background = C.hover)}
        onMouseLeave={e => (e.currentTarget.style.background = C.card)}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: meta.dot }} />
          <span style={{ fontSize: 15, fontWeight: 500, color: C.text }}>{meta.label}</span>
        </span>
        <span style={{ fontSize: 11, color: C.muted }}>▾</span>
      </button>

      {open && (
        <div style={{
          position:     'absolute',
          top:          '108%',
          left:         0,
          right:        0,
          background:   C.card,
          border:       `1px solid ${C.border}`,
          borderRadius: 11,
          padding:      6,
          boxShadow:    '0 12px 32px -10px rgba(15,15,15,0.18)',
          zIndex:       50,
        }}>
          {STAGES.map(s => {
            const active = s.key === value;
            return (
              <button
                key={s.key}
                onClick={() => { onChange(s.key); setOpen(false); }}
                style={{
                  display:    'flex',
                  alignItems: 'center',
                  gap:        9,
                  width:      '100%',
                  padding:    '8px 10px',
                  borderRadius: 8,
                  background: active ? C.chipBg : 'transparent',
                  border:     'none',
                  cursor:     'pointer',
                  fontSize:   13.5,
                  fontWeight: active ? 600 : 500,
                  color:      C.text,
                  fontFamily: FONT,
                  textAlign:  'left',
                }}
                onMouseEnter={e => { if (!active) e.currentTarget.style.background = C.hover; }}
                onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
              >
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: s.dot }} />
                {s.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Topic chips (editable keywords) ───────────────────────────────────────────

function TopicChips({ chips, onChange }: {
  chips: string[]; onChange: (next: string[]) => void;
}) {
  const [adding, setAdding] = React.useState(false);
  const [draft, setDraft]   = React.useState('');
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => { if (adding) inputRef.current?.focus(); }, [adding]);

  const commit = () => {
    const v = draft.trim();
    if (v && !chips.includes(v)) onChange([...chips, v]);
    setDraft('');
    setAdding(false);
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
      {chips.map(chip => (
        <span
          key={chip}
          style={{
            display:      'inline-flex',
            alignItems:   'center',
            gap:          5,
            padding:      '4px 9px',
            borderRadius: 7,
            background:   C.chipBg,
            border:       `1px solid ${C.chipBorder}`,
            fontSize:     14,
            fontWeight:   500,
            color:        C.textSoft,
            fontFamily:   FONT,
          }}
        >
          {chip}
          <button
            onClick={() => onChange(chips.filter(c => c !== chip))}
            title="Remove"
            style={{
              border: 'none', background: 'none', cursor: 'pointer',
              color: C.faint, fontSize: 13, lineHeight: 1, padding: 0,
            }}
          >
            ×
          </button>
        </span>
      ))}

      {adding ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={e => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') { setDraft(''); setAdding(false); }
          }}
          placeholder="Topic…"
          style={{
            width:        76,
            padding:      '4px 8px',
            borderRadius: 7,
            border:       `1px solid ${C.chipBorder}`,
            background:   C.card,
            fontSize:     14,
            color:        C.text,
            fontFamily:   FONT,
            outline:      'none',
          }}
        />
      ) : (
        <button
          onClick={() => setAdding(true)}
          title="Add topic"
          style={{
            display:      'inline-flex',
            alignItems:   'center',
            justifyContent: 'center',
            width:        26,
            height:       26,
            borderRadius: 7,
            background:   'transparent',
            border:       `1px dashed ${C.chipBorder}`,
            cursor:       'pointer',
            color:        C.muted,
            fontSize:     14,
            lineHeight:   1,
          }}
          onMouseEnter={e => (e.currentTarget.style.background = C.hover)}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        >
          +
        </button>
      )}
    </div>
  );
}

// ─── Action row ────────────────────────────────────────────────────────────────

function ActionRow({ icon, label, onClick }: {
  icon: string; label: string; onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display:        'flex',
        alignItems:     'center',
        gap:            10,
        width:          '100%',
        padding:        '9px 11px',
        borderRadius:   9,
        background:     C.card,
        border:         `1px solid ${C.borderSoft}`,
        cursor:         'pointer',
        fontFamily:     FONT,
        textAlign:      'left',
        transition:     'background 0.12s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = C.hover)}
      onMouseLeave={e => (e.currentTarget.style.background = C.card)}
    >
      <span style={{ fontSize: 14, width: 18, textAlign: 'center', fontFamily: EMOJI_FONT }}>{icon}</span>
      <span style={{ flex: 1, fontSize: 14.5, fontWeight: 500, color: C.text }}>{label}</span>
      <span style={{ fontSize: 13, color: C.faint }}>→</span>
    </button>
  );
}

// ─── Linked chip ───────────────────────────────────────────────────────────────

function LinkChip({ icon, label }: { icon: string; label: string }) {
  return (
    <span style={{
      display:      'inline-flex',
      alignItems:   'center',
      gap:          6,
      padding:      '5px 10px',
      borderRadius: 8,
      background:   C.card,
      border:       `1px solid ${C.chipBorder}`,
      fontSize:     12.5,
      fontWeight:   500,
      color:        C.textSoft,
      fontFamily:   FONT,
      cursor:       'pointer',
    }}>
      <span style={{ fontFamily: EMOJI_FONT, fontSize: 12 }}>{icon}</span>
      {label}
    </span>
  );
}

// ─── Source card ───────────────────────────────────────────────────────────────

function SourceCard({ source, fallbackDate }: {
  source: NoteSource | null; fallbackDate: string;
}) {
  // No captured source on record → quiet "added directly" card, no fabrication.
  const channel = source?.channel ?? 'Trumpet';
  const cfg     = sourceConfig(channel);
  const who     = source?.who ?? 'Me';
  const when    = source?.at
    ? (source.at.includes('T') ? relativeTime(source.at) : source.at)
    : `Added directly · ${new Date(fallbackDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
  const preview = source?.preview;

  return (
    <div style={{
      border:       `1px solid ${C.border}`,
      borderRadius: 12,
      padding:      12,
      background:   C.card,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width:          30,
          height:         30,
          borderRadius:   8,
          background:     cfg.bg,
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          fontSize:       14,
          color:          '#fff',
          flexShrink:     0,
        }}>
          <span style={{ fontFamily: EMOJI_FONT }}>{cfg.glyph}</span>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: C.text }}>{cfg.label}</div>
          <div style={{ fontSize: 12.5, color: C.muted }}>{when}{source ? ` · ${who}` : ''}</div>
        </div>
      </div>

      {preview && (
        <div style={{
          marginTop:   10,
          fontSize:    12.5,
          lineHeight:  1.5,
          color:       C.textSoft,
          display:     '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow:    'hidden',
        }}>
          {preview}
        </div>
      )}

      {source?.url && (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer"
          style={{
            marginTop:   10,
            display:     'flex',
            alignItems:  'center',
            justifyContent: 'space-between',
            fontSize:    12.5,
            fontWeight:  600,
            color:       C.textSoft,
            textDecoration: 'none',
          }}
        >
          Open original
          <span style={{ color: C.muted }}>↗</span>
        </a>
      )}
    </div>
  );
}

// ─── Main editor ───────────────────────────────────────────────────────────────

interface NoteEditorProps {
  note:    NoteRecord;
  onClose: (refreshNeeded: boolean) => void;
}

export function NoteEditor({ note, onClose }: NoteEditorProps) {
  const initialMeta = React.useMemo(() => parseMeta(note.notes), [note.notes]);

  // ── State ──
  const [title,    setTitle]    = React.useState(note.title);
  const [body,     setBody]     = React.useState(() => note.body || initialMeta.body || note.summary);
  const [category, setCategory] = React.useState<NoteCategory>(note.category);
  const [chips,    setChips]    = React.useState<string[]>(() => toChips(note.keywords));
  const [stage,    setStage]    = React.useState<Stage>(initialMeta.stage);
  const [saving,   setSaving]   = React.useState(false);
  const [saved,    setSaved]    = React.useState(false);

  const color  = initialMeta.color || CATEGORY_CONFIG[note.category].stickyColor;
  const source = initialMeta.source;

  // ── Refs — always hold latest values so callbacks never go stale ──
  const latestTitle    = React.useRef(title);
  const latestBody     = React.useRef(body);
  const latestCategory = React.useRef(category);
  const latestChips    = React.useRef(chips);
  const latestStage    = React.useRef(stage);
  const savedRef       = React.useRef(false);
  const userEditedBody = React.useRef(false);   // don't let a late file-load clobber typing
  const baselineBody   = React.useRef(body);    // body as last loaded/saved — for change detection
  const debounceTimer  = React.useRef<ReturnType<typeof setTimeout>>();

  React.useEffect(() => { latestTitle.current    = title;    }, [title]);
  React.useEffect(() => { latestBody.current     = body;     }, [body]);
  React.useEffect(() => { latestCategory.current = category; }, [category]);
  React.useEffect(() => { latestChips.current    = chips;    }, [chips]);
  React.useEffect(() => { latestStage.current    = stage;    }, [stage]);

  // ── Core save (uses refs — always current) ────────────────────────────────
  const performSave = React.useCallback(async (): Promise<void> => {
    setSaving(true);
    try {
      const bodyVal = latestBody.current;
      // Body lives in its own column (no length cap); notes JSON holds metadata only.
      const meta    = JSON.stringify({ pinned: note.pinned, color, stage: latestStage.current, source });
      const summary = firstSentences(bodyVal, 2) || note.summary;
      await client.records.update(TABLES.noteIndex, note.id, {
        title:    latestTitle.current,
        summary,
        category: latestCategory.current,
        keywords: fromChips(latestChips.current),
        body:     bodyVal,
        notes:    meta,
      } as never);
      savedRef.current = true;
      baselineBody.current = bodyVal;
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }, [note, color, source]);

  // ── Debounced auto-save (800 ms after last change) ────────────────────────
  const scheduleSave = React.useCallback(() => {
    clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => { void performSave(); }, 800);
  }, [performSave]);

  React.useEffect(() => () => { clearTimeout(debounceTimer.current); }, []);

  // ── Close — always flushes & awaits save first ────────────────────────────
  const handleClose = React.useCallback(async () => {
    clearTimeout(debounceTimer.current);
    const anyChange =
      latestTitle.current        !== note.title    ||
      latestBody.current         !== baselineBody.current ||
      latestCategory.current     !== note.category ||
      fromChips(latestChips.current) !== note.keywords ||
      latestStage.current        !== initialMeta.stage;

    if (anyChange) await performSave();
    onClose(anyChange || savedRef.current);
  }, [note, performSave, onClose, initialMeta]);

  // ── Cmd/Ctrl+S ────────────────────────────────────────────────────────────
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        void performSave();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [performSave]);

  // ── Field change handlers ─────────────────────────────────────────────────
  const onTitleChange    = (v: string)       => { setTitle(v);    scheduleSave(); };
  const onBodyChange     = (v: string)       => { userEditedBody.current = true; setBody(v); scheduleSave(); };
  const onCategoryChange = (v: NoteCategory) => { setCategory(v); scheduleSave(); };
  const onChipsChange    = (v: string[])     => { setChips(v);    scheduleSave(); };
  const onStageChange    = (v: Stage)        => { setStage(v);    scheduleSave(); };

  // Title is line 1 of the note; the rest is the body. We keep a derived
  // subtitle from the note summary as a quiet description under the title.
  const subtitle = note.summary && note.summary !== body ? note.summary : '';
  const bullets  = aiBullets(note.summary);
  const catCfg   = CATEGORY_CONFIG[category];

  // Rendered through a portal to <body> so it escapes the scaled 1512×1008
  // Stage canvas and fills the real browser viewport — a true full-page editor,
  // not a letterboxed container.
  return createPortal(
    <motion.div
      initial={{ x: '100%' }}
      animate={{ x: 0 }}
      exit={{ x: '100%' }}
      transition={{ duration: 0.28, ease: [0.25, 0.46, 0.45, 0.94] }}
      style={{
        position:      'fixed',
        inset:         0,
        background:    C.appBg,
        zIndex:        4000,
        display:       'flex',
        flexDirection: 'column',
        fontFamily:    FONT,
        color:         C.text,
      }}
    >
      {/* ── Top bar ── */}
      <div style={{
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'space-between',
        padding:        '0 18px',
        height:         56,
        borderBottom:   `1px solid ${C.border}`,
        flexShrink:     0,
      }}>
        {/* Left: back + Notes — ~20% larger than the other top-bar controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 200 }}>
          <button
            title="Back to notes"
            onClick={() => void handleClose()}
            onMouseEnter={e => (e.currentTarget.style.background = C.hover)}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            style={{
              display:        'flex',
              alignItems:     'center',
              justifyContent: 'center',
              width:          39,
              height:         39,
              borderRadius:   9,
              background:     'transparent',
              border:         'none',
              cursor:         'pointer',
              color:          C.textSoft,
              transition:     'background 0.12s',
            }}
          >
            <span style={{ fontSize: 22 }}>←</span>
          </button>
          <span style={{ fontSize: 18, fontWeight: 600, color: C.text }}>Notes</span>
        </div>

        {/* Center: collection */}
        <CollectionDropdown value={category} onChange={onCategoryChange} />

        {/* Right: tools + save */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 200, justifyContent: 'flex-end' }}>
          {saving && <span style={{ fontSize: 12.5, color: C.muted, marginRight: 4 }}>Saving…</span>}
          {saved && !saving && <span style={{ fontSize: 12.5, color: C.green, marginRight: 4 }}>✓ Saved</span>}
          <IconButton title="Search">🔍</IconButton>
          <IconButton title="Copy link">🔗</IconButton>
          <IconButton title="Share">👤</IconButton>
          <IconButton title="More">⋯</IconButton>
          <button
            onClick={() => void performSave()}
            disabled={saving}
            style={{
              marginLeft:   6,
              padding:      '7px 18px',
              borderRadius: 8,
              background:   C.saveBg,
              border:       'none',
              cursor:       saving ? 'default' : 'pointer',
              fontSize:     13.5,
              fontWeight:   600,
              color:        C.saveFg,
              fontFamily:   FONT,
              opacity:      saving ? 0.6 : 1,
            }}
          >
            Save
          </button>
        </div>
      </div>

      {/* ── Body ── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Left context sidebar */}
        <div style={{
          width:         320,
          borderRight:   `1px solid ${C.border}`,
          background:    C.sidebarBg,
          padding:       '22px 20px',
          display:       'flex',
          flexDirection: 'column',
          gap:           24,
          overflowY:     'auto',
          flexShrink:    0,
        }}>
          {/* Source */}
          <section>
            <SectionLabel>Source</SectionLabel>
            <SourceCard source={source} fallbackDate={note.created_at} />
          </section>

          {/* Stage */}
          <section>
            <SectionLabel>Stage</SectionLabel>
            <StageRow value={stage} onChange={onStageChange} />
          </section>

          {/* Topics */}
          <section>
            <SectionLabel>Topics</SectionLabel>
            <TopicChips chips={chips} onChange={onChipsChange} />
          </section>

          {/* AI read */}
          {bullets.length > 0 && (
            <section>
              <SectionLabel>✦ AI read</SectionLabel>
              <div style={{
                border:       `1px solid ${C.border}`,
                borderRadius: 12,
                padding:      '12px 13px',
                background:   C.card,
                display:      'flex',
                flexDirection:'column',
                gap:          7,
              }}>
                {bullets.map((b, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, lineHeight: 1.5, color: C.textSoft }}>
                    <span style={{ color: C.faint }}>•</span>
                    <span>{b}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Actions */}
          <section>
            <SectionLabel>Actions</SectionLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              <ActionRow icon="📝" label="Turn into outline" />
              <ActionRow icon="✅" label="Create checklist" />
              <ActionRow icon="🔗" label="Link to a project" />
            </div>
          </section>

          {/* Linked to */}
          <section>
            <SectionLabel>Linked to</SectionLabel>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 8 }}>
              <LinkChip icon={catCfg.emoji} label={catCfg.label} />
            </div>
            <button
              style={{
                display:    'flex',
                alignItems: 'center',
                gap:        7,
                background: 'none',
                border:     'none',
                cursor:     'pointer',
                fontSize:   12.5,
                fontWeight: 500,
                color:      C.muted,
                fontFamily: FONT,
                padding:    0,
              }}
            >
              <span style={{ fontSize: 14 }}>+</span> Link
            </button>
          </section>

          {/* Metadata — quiet, at the bottom */}
          <section style={{ marginTop: 'auto', paddingTop: 8 }}>
            <div style={{ fontSize: 13, color: C.muted, lineHeight: 1.7 }}>
              Created {new Date(note.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              <br />
              Edited {relativeTime(note.updated_at)}
            </div>
          </section>
        </div>

        {/* Main writing canvas — wide, full-bleed, generous padding */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div style={{
            maxWidth:  980,
            margin:    '0 auto',
            padding:   '76px 88px 200px',
            display:   'flex',
            flexDirection: 'column',
          }}>
            {/* Title — wraps, never clips */}
            <AutoGrowTextarea
              value={title}
              onChange={e => onTitleChange(e.target.value)}
              placeholder="Untitled"
              style={{
                fontSize:      48,
                fontWeight:    800,
                letterSpacing: -1.2,
                color:         C.text,
                fontFamily:    FONT,
                background:    'none',
                border:        'none',
                outline:       'none',
                width:         '100%',
                lineHeight:    1.18,
                padding:       0,
                display:       'block',
              }}
            />

            {/* Subtitle / description */}
            {subtitle && (
              <div style={{
                marginTop:  16,
                fontSize:   21,
                lineHeight: 1.5,
                color:      C.textSoft,
                fontWeight: 400,
              }}>
                {subtitle}
              </div>
            )}

            {/* Divider */}
            <div style={{ height: 1, background: C.border, margin: '32px 0 16px' }} />

            {/* Body */}
            <AutoGrowTextarea
              value={body}
              onChange={e => onBodyChange(e.target.value)}
              placeholder="Start writing or type / for commands"
              style={{
                minHeight:  440,
                fontSize:   19,
                lineHeight: 1.85,
                color:      C.text,
                fontFamily: FONT,
                background: 'none',
                border:     'none',
                outline:    'none',
                width:      '100%',
                padding:    '4px 0 0',
                display:    'block',
              }}
            />
          </div>
        </div>
      </div>
    </motion.div>,
    document.body,
  );
}
