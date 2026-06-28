import * as React from 'react';
import { motion } from 'framer-motion';
import { useRecords } from 'lemma-sdk/react';
import { client } from '@/lib/client';
import { runtimeConfig } from '@/lib/runtime-config';
import { TABLES } from '@/lib/resources';
import { tokens } from '@/lib/tokens';
import { dueDateLabel } from '@/lib/time';
import { AvatarCircle } from './AvatarCircle';
import { PLACEHOLDER_AVATARS } from './PeopleSubtab';
import type { PersonRecord } from '@/hooks/usePeople';

type CommitTab = 'open' | 'done';

const URGENCY_COLORS = { red: '#f5402c', amber: '#f3b223', green: '#57b86a' };

// ── Commitment row ────────────────────────────────────────────────────────────

function CommitmentRow({ title, dueDate, type }: {
  title:    string;
  dueDate?: string;
  type:     string;
}) {
  const { label, urgency } = dueDateLabel(dueDate ?? null);
  const isOutbound = type === 'to_others';

  return (
    <div style={{
      display:      'flex',
      alignItems:   'center',
      gap:          12,
      padding:      '13px 0',
      borderBottom: '1px solid var(--trumpet-edge-sm)',
    }}>
      <span style={{
        fontSize:     13,
        color:        isOutbound ? tokens.green : tokens.amber,
        flexShrink:   0,
        fontWeight:   700,
      }}>
        {isOutbound ? '↑' : '↓'}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize:     15,
          fontWeight:   600,
          color:        tokens.fg,
          whiteSpace:   'nowrap',
          overflow:     'hidden',
          textOverflow: 'ellipsis',
        }}>
          {title}
        </div>
      </div>
      {label && (
        <span style={{
          fontSize:     12,
          fontWeight:   600,
          color:        URGENCY_COLORS[urgency],
          flexShrink:   0,
          background:   `${URGENCY_COLORS[urgency]}18`,
          padding:      '3px 9px',
          borderRadius: 99,
        }}>
          {label}
        </span>
      )}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function CommitmentsEmpty({ tab }: { tab: CommitTab }) {
  return (
    <div style={{
      flex:           1,
      display:        'flex',
      flexDirection:  'column',
      alignItems:     'center',
      justifyContent: 'center',
      gap:            16,
      paddingBottom:  32,
      color:          tokens.muted,
    }}>
      <div style={{
        width:        64,
        height:       64,
        borderRadius: '50%',
        border:       '2px solid var(--trumpet-edge-strong)',
        display:      'flex',
        alignItems:   'center',
        justifyContent: 'center',
      }}>
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      </div>
      <div style={{ textAlign: 'center', lineHeight: 1.6 }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: tokens.fg }}>All caught up</div>
        <div style={{ fontSize: 13, marginTop: 4 }}>
          {tab === 'open'
            ? 'No open commitments with this person.'
            : 'No completed commitments yet.'}
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface PersonDetailProps {
  person:         PersonRecord;
  placeholderIdx: number;
  onClose:        (refreshNeeded: boolean) => void;
}

export function PersonDetail({ person, placeholderIdx, onClose }: PersonDetailProps) {
  const avatarSrc = person.photo_url
    || (placeholderIdx < PLACEHOLDER_AVATARS.length ? PLACEHOLDER_AVATARS[placeholderIdx] : undefined);
  const [commitTab,    setCommitTab]    = React.useState<CommitTab>('open');
  const [email,        setEmail]        = React.useState(person.email ?? '');
  const [agentNotes,   setAgentNotes]   = React.useState(person.agentNotes ?? '');
  const [editingEmail, setEditingEmail] = React.useState(false);
  const [saving,       setSaving]       = React.useState(false);
  const [saved,        setSaved]        = React.useState(false);

  const latestEmail      = React.useRef(email);
  const latestAgentNotes = React.useRef(agentNotes);
  const savedRef         = React.useRef(false);
  const debounceTimer    = React.useRef<ReturnType<typeof setTimeout>>();

  React.useEffect(() => { latestEmail.current      = email;      }, [email]);
  React.useEffect(() => { latestAgentNotes.current = agentNotes; }, [agentNotes]);

  const commitState = useRecords<Record<string, unknown>>({
    client,
    podId:     runtimeConfig.podId,
    tableName: TABLES.commitments,
    filters:   [{ field: 'person_id', operator: 'eq', value: person.id }],
    limit:     100,
  });

  const allCommits  = commitState.records;
  const openCommits = allCommits.filter(r =>
    r.status === 'active' && (r.type === 'to_others' || r.type === 'from_others')
  );
  const doneCommits = allCommits.filter(r =>
    r.status === 'completed' && (r.type === 'to_others' || r.type === 'from_others')
  );

  const performSave = React.useCallback(async () => {
    setSaving(true);
    try {
      await client.records.update(TABLES.people, person.id, {
        email: latestEmail.current,
        notes: JSON.stringify({ agentNotes: latestAgentNotes.current }),
      } as never);
      savedRef.current = true;
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }, [person.id]);

  const scheduleSave = React.useCallback(() => {
    clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => { void performSave(); }, 600);
  }, [performSave]);

  React.useEffect(() => () => { clearTimeout(debounceTimer.current); }, []);

  const handleClose = React.useCallback(async () => {
    clearTimeout(debounceTimer.current);
    const changed =
      latestEmail.current      !== (person.email      ?? '') ||
      latestAgentNotes.current !== (person.agentNotes ?? '');
    if (changed) await performSave();
    onClose(changed || savedRef.current);
  }, [person, performSave, onClose]);

  const handleAvatarUpload = React.useCallback(async (file: File) => {
    const result = await client.files.upload(file, {
      directoryPath: '/pod/photos',
      name: `${person.id}${file.name.slice(file.name.lastIndexOf('.'))}`,
    });
    await client.records.update(TABLES.people, person.id, {
      photo_url: result.path,
    } as never);
    savedRef.current = true;
    onClose(true);
  }, [person.id, onClose]);

  const listToShow = commitTab === 'open' ? openCommits : doneCommits;

  return (
    <motion.div
      initial={{ x: '100%' }}
      animate={{ x: 0 }}
      exit={{ x: '100%' }}
      transition={{ duration: 0.28, ease: [0.25, 0.46, 0.45, 0.94] }}
      style={{
        position:      'absolute',
        inset:         0,
        background:    tokens.bg,
        zIndex:        200,
        display:       'flex',
        flexDirection: 'column',
        fontFamily:    tokens.font,
        overflow:      'hidden',
      }}
    >
      {/* ── Floating back button ── */}
      <div style={{
        position:   'absolute',
        top:        24,
        left:       28,
        zIndex:     10,
        display:    'flex',
        alignItems: 'center',
        gap:        12,
      }}>
        <button
          onClick={() => void handleClose()}
          style={{
            display:      'flex',
            alignItems:   'center',
            gap:          7,
            background:   'var(--trumpet-chip-bg)',
            border:       '1px solid var(--trumpet-edge-strong)',
            borderRadius: 99,
            padding:      '8px 16px 8px 12px',
            cursor:       'pointer',
            color:        tokens.fg,
            fontSize:     14,
            fontWeight:   700,
            fontFamily:   tokens.font,
            backdropFilter: 'blur(8px)',
            transition:   'background 0.15s',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 18-6-6 6-6"/>
          </svg>
          People
        </button>

        {(saving || saved) && (
          <span style={{
            fontSize:   13,
            fontWeight: 500,
            color:      saved ? tokens.green : tokens.muted,
          }}>
            {saving ? 'Saving…' : '✓ Saved'}
          </span>
        )}
      </div>

      {/* ── Body ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', paddingTop: 0 }}>

        {/* ── Left panel ── */}
        <div style={{
          width:         340,
          flexShrink:    0,
          borderRight:   '1px solid var(--trumpet-edge-sm)',
          display:       'flex',
          flexDirection: 'column',
          overflowY:     'auto',
          scrollbarWidth: 'none',
        }}>
          {/* Hero illustration area — flat beige */}
          <div style={{
            position:       'relative',
            height:         240,
            background:     'var(--trumpet-bg2)',
            display:        'flex',
            flexDirection:  'column',
            alignItems:     'center',
            justifyContent: 'flex-end',
            paddingBottom:  20,
            overflow:       'hidden',
            flexShrink:     0,
          }}>
            <AvatarCircle
              src={avatarSrc}
              name={person.name}
              size={110}
              onUpload={handleAvatarUpload}
              style={{ border: '4px solid var(--trumpet-bg)' }}
            />
          </div>

          {/* Name + role */}
          <div style={{
            padding:   '20px 32px 0',
            flexShrink: 0,
          }}>
            <div style={{
              fontSize:      26,
              fontWeight:    800,
              letterSpacing: -0.6,
              color:         tokens.fg,
              lineHeight:    1.15,
            }}>
              {person.name}
            </div>
            {person.role && (
              <div style={{
                fontSize:      12,
                fontWeight:    700,
                letterSpacing: 1.2,
                textTransform: 'uppercase' as const,
                color:         '#57b86a',
                marginTop:     5,
              }}>
                {person.role}
              </div>
            )}
          </div>

          {/* Email */}
          <div style={{ padding: '16px 32px 0', flexShrink: 0 }}>
            {editingEmail ? (
              <input
                autoFocus
                value={email}
                onChange={e => { setEmail(e.target.value); scheduleSave(); }}
                onBlur={() => setEditingEmail(false)}
                placeholder="name@email.com"
                style={{
                  width:        '100%',
                  background:   'var(--trumpet-surface)',
                  border:       '1px solid var(--trumpet-edge-strong)',
                  borderRadius: 10,
                  padding:      '9px 12px',
                  fontSize:     14,
                  color:        tokens.fg,
                  fontFamily:   tokens.font,
                  outline:      'none',
                  boxSizing:    'border-box',
                }}
              />
            ) : (
              <div
                onClick={() => setEditingEmail(true)}
                style={{
                  display:    'flex',
                  alignItems: 'center',
                  gap:        8,
                  cursor:     'text',
                  padding:    '3px 0',
                }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={email ? '#57b86a' : tokens.muted} strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
                  <rect x="2" y="4" width="20" height="16" rx="2"/>
                  <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
                </svg>
                <span style={{
                  fontSize:     14,
                  color:        email ? tokens.fg : tokens.muted,
                  overflow:     'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace:   'nowrap',
                }}>
                  {email || 'Add email…'}
                </span>
              </div>
            )}
          </div>

          {/* Divider */}
          <div style={{ height: 1, background: 'var(--trumpet-divider)', margin: '20px 32px 0' }} />

          {/* Context for Trumpet */}
          <div style={{ padding: '20px 32px', flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{
              display:    'flex',
              alignItems: 'center',
              gap:        7,
              marginBottom: 10,
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#57b86a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
              </svg>
              <span style={{
                fontSize:      11,
                fontWeight:    700,
                letterSpacing: 1.1,
                textTransform: 'uppercase' as const,
                color:         tokens.muted,
              }}>
                Context for Trumpet
              </span>
            </div>

            <div style={{
              flex:         1,
              background:   'rgba(87,184,106,0.05)',
              border:       '1px solid rgba(87,184,106,0.15)',
              borderRadius: 14,
              padding:      '2px',
            }}>
              <textarea
                value={agentNotes}
                onChange={e => { setAgentNotes(e.target.value); scheduleSave(); }}
                placeholder={'Routing notes for Trumpet…\n\ne.g. "Always CC their EA"\n"Use formal tone"\n"Follow up after 2 days"'}
                style={{
                  width:          '100%',
                  height:         '100%',
                  minHeight:      120,
                  fontSize:       13,
                  lineHeight:     1.65,
                  color:          tokens.fg,
                  fontFamily:     tokens.font,
                  background:     'transparent',
                  border:         'none',
                  borderRadius:   12,
                  padding:        '12px 14px',
                  resize:         'none',
                  outline:        'none',
                  scrollbarWidth: 'none',
                  boxSizing:      'border-box',
                }}
              />
            </div>
          </div>
        </div>

        {/* ── Right panel — Commitments ── */}
        <div style={{
          flex:          1,
          padding:       '80px 52px 40px',
          display:       'flex',
          flexDirection: 'column',
          overflow:      'hidden',
        }}>
          <div style={{
            fontSize:      11,
            fontWeight:    700,
            letterSpacing: 1.4,
            textTransform: 'uppercase' as const,
            color:         tokens.muted,
            marginBottom:  16,
            flexShrink:    0,
          }}>
            Commitments
          </div>

          {/* Subtab bar */}
          <div style={{
            display:      'flex',
            gap:          4,
            marginBottom: 24,
            flexShrink:   0,
            borderBottom: '1px solid var(--trumpet-edge-sm)',
            paddingBottom: 0,
          }}>
            {(['open', 'done'] as CommitTab[]).map(t => {
              const active = commitTab === t;
              const count  = t === 'open' ? openCommits.length : doneCommits.length;
              return (
                <button
                  key={t}
                  onClick={() => setCommitTab(t)}
                  style={{
                    display:      'flex',
                    alignItems:   'center',
                    gap:          7,
                    padding:      '9px 16px',
                    background:   'none',
                    border:       'none',
                    borderBottom: active
                      ? '2px solid var(--trumpet-nav-active)'
                      : '2px solid transparent',
                    marginBottom: '-1px',
                    cursor:       'pointer',
                    fontSize:     13,
                    fontWeight:   700,
                    letterSpacing: 0.4,
                    color:        active ? tokens.fg : tokens.muted,
                    fontFamily:   tokens.font,
                    textTransform: 'uppercase' as const,
                    transition:   'color 0.15s',
                  }}
                >
                  {t}
                  {count > 0 && (
                    <span style={{
                      fontSize:     11,
                      fontWeight:   600,
                      color:        active ? tokens.muted : 'var(--trumpet-chip-fg)',
                      background:   'var(--trumpet-surface)',
                      padding:      '1px 7px',
                      borderRadius: 99,
                      border:       '1px solid var(--trumpet-edge-sm)',
                    }}>
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Commitment list or empty state */}
          <div style={{ flex: 1, overflowY: 'auto', scrollbarWidth: 'none', display: 'flex', flexDirection: 'column' }}>
            {commitState.isLoading ? (
              <div style={{ fontSize: 14, color: tokens.muted }}>Loading…</div>
            ) : listToShow.length === 0 ? (
              <CommitmentsEmpty tab={commitTab} />
            ) : (
              listToShow.map(r => (
                <CommitmentRow
                  key={r.id as string}
                  title={r.title as string}
                  dueDate={r.due_date as string | undefined}
                  type={r.type as string}
                />
              ))
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
