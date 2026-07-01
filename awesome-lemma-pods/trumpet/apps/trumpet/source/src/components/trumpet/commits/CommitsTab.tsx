/**
 * CommitsTab — full commitments view.
 *
 * Left:  Trumpet voice widget — conversational summary that cycles through templates
 * Right: Tabbed card — From your end (outbound) | To you (inbound)
 *        Rows grouped: Overdue → Today → Upcoming
 */
import * as React from 'react';
import gsap from 'gsap';
import { tokens } from '@/lib/tokens';
import { client } from '@/lib/client';
import { TABLES } from '@/lib/resources';
import { useCommitments, completeCommitment } from '@/hooks/useCommitments';
import type { Commitment } from '@/hooks/useCommitments';
import { TrumpetSkeleton } from '../shared/TrumpetSkeleton';
import { CrossedFingersIcon, HandshakeIcon } from '../shared/TrumpetIcons';
import { PokeModal, PokeIcon } from './PokeModal';

const TAB_ICON_COLOR: Record<string, string> = {
  mine:  '#c9952a', // warm amber — outbound / handshake
  yours: '#9070b8', // soft lilac — inbound / crossed fingers
};

// ── Template engine ───────────────────────────────────────────────────────────

interface CommitState {
  totalOut:   number;
  totalIn:    number;
  overdueOut: number;
  overdueIn:  number;
  todayOut:   number;
  todayIn:    number;
  firstOverduePerson?: string;
  firstTodayPerson?:   string;
  firstInPerson?:      string;
}

type TemplateFn = (s: CommitState) => string;

// Overdue — someone is waiting on you
const OVERDUE_TEMPLATES: TemplateFn[] = [
  s => `${s.overdueOut} thing${s.overdueOut > 1 ? 's' : ''} on your end ${s.overdueOut > 1 ? 'have' : 'has'} slipped past the date.${s.firstOverduePerson ? ` ${s.firstOverduePerson} is still waiting.` : ''} Worth clearing those first.`,
  s => `You've got ${s.overdueOut} overdue commitment${s.overdueOut > 1 ? 's' : ''}.${s.firstOverduePerson ? ` ${s.firstOverduePerson} is holding out for you.` : ''} Let's not let them pile up.`,
  s => s.firstOverduePerson
    ? `${s.firstOverduePerson} has been waiting — that one's past due. You've got ${s.overdueOut} overdue in total.`
    : `${s.overdueOut} commitment${s.overdueOut > 1 ? 's are' : ' is'} past due. Quick sweep could close these out.`,
  s => `Overdue count: ${s.overdueOut}.${s.firstOverduePerson ? ` ${s.firstOverduePerson} is on the list.` : ''} The longer these sit, the messier they get.`,
  s => `${s.overdueOut > 1 ? `${s.overdueOut} things are` : 'One thing is'} past deadline on your end.${s.totalIn > 0 ? ` Meanwhile, ${s.totalIn} people have commitments to you.` : ''} Your overdue items take priority.`,
];

// All clear
const CLEAR_TEMPLATES: TemplateFn[] = [
  () => `Slate's clean. Nothing owed, nothing overdue. Genuinely rare — enjoy it.`,
  () => `You're completely caught up. No open threads, no overdue items. Good position to be in.`,
  () => `Nothing active right now. Ask Trumpet to log a commitment when you make one.`,
  () => `All clear across the board. No outbound, no inbound, nothing overdue.`,
  () => `Clean slate. No commitments in the queue at all.`,
];

// Balanced — both outbound and inbound
const BALANCED_TEMPLATES: TemplateFn[] = [
  s => `You're holding ${s.totalOut} open thread${s.totalOut > 1 ? 's' : ''} and ${s.totalIn} ${s.totalIn > 1 ? 'are' : 'is'} coming your way.${s.todayOut > 0 ? ` ${s.todayOut} of yours land today.` : ''}`,
  s => `${s.totalOut} outbound, ${s.totalIn} inbound.${s.todayOut > 0 ? ` ${s.todayOut} due today from your end.` : ' Nothing due today.'} Things look manageable.`,
  s => s.firstInPerson
    ? `${s.firstInPerson} owes you something. You've got ${s.totalOut} open on your end too. Keep an eye on both sides.`
    : `You've got ${s.totalOut} to give and ${s.totalIn} coming to you. Stay on top of the ones due soon.`,
  s => `${s.totalOut} commitments from you, ${s.totalIn} to you.${s.todayOut + s.todayIn > 0 ? ` ${s.todayOut + s.todayIn} of those land today.` : ''} Solid pipeline.`,
  s => `Open threads: ${s.totalOut} outbound, ${s.totalIn} inbound.${s.firstTodayPerson ? ` Something for ${s.firstTodayPerson} is due today.` : ''} Nothing overdue — good shape.`,
];

// Only outbound
const OUTBOUND_TEMPLATES: TemplateFn[] = [
  s => `You've got ${s.totalOut} commitment${s.totalOut > 1 ? 's' : ''} out.${s.todayOut > 0 ? ` ${s.todayOut} due today.` : ' None due today.'} Nothing coming your way yet.`,
  s => `${s.totalOut} open on your end.${s.firstTodayPerson ? ` Something for ${s.firstTodayPerson} lands today.` : ''}${s.totalOut > 3 ? ' Quite a few threads open.' : ' Manageable.'}`,
  s => `All active commitments are outbound — ${s.totalOut} in total. No one's got anything pending for you.`,
  s => `You're carrying ${s.totalOut} thing${s.totalOut > 1 ? 's' : ''}.${s.todayOut > 0 ? ` Today's the deadline for ${s.todayOut} of them.` : ''} Nobody owes you anything right now.`,
];

// Only inbound
const INBOUND_TEMPLATES: TemplateFn[] = [
  s => `${s.totalIn} person${s.totalIn > 1 ? 's have' : ' has'} commitments to you.${s.firstInPerson ? ` ${s.firstInPerson} is on that list.` : ''} Nothing you owe right now.`,
  s => `Your side is clear — no outbound. But ${s.totalIn} ${s.totalIn > 1 ? 'people are' : 'person is'} expected to deliver something to you.`,
  s => s.firstInPerson
    ? `${s.firstInPerson} owes you something. You've got ${s.totalIn} inbound in total, nothing outbound.`
    : `${s.totalIn} inbound commitment${s.totalIn > 1 ? 's' : ''} heading your way. Your own slate is clean.`,
];

function pickTemplate(s: CommitState, seed: number): string {
  const pick = (arr: TemplateFn[]) => arr[seed % arr.length](s);

  if (s.overdueOut > 0) return pick(OVERDUE_TEMPLATES);
  if (s.totalOut === 0 && s.totalIn === 0) return pick(CLEAR_TEMPLATES);
  if (s.totalOut > 0 && s.totalIn > 0) return pick(BALANCED_TEMPLATES);
  if (s.totalOut > 0) return pick(OUTBOUND_TEMPLATES);
  return pick(INBOUND_TEMPLATES);
}

// ── Trumpet voice widget ──────────────────────────────────────────────────────────

function TrumpetVoiceWidget({
  commitments, isLoading,
}: {
  commitments: ReturnType<typeof useCommitments>;
  isLoading:   boolean;
}) {
  // Seed changes on each mount / after refresh — stable within a render cycle
  const seedRef = React.useRef(Math.floor(Math.random() * 100));

  const state: CommitState = React.useMemo(() => {
    const { outbound, inbound } = commitments;
    const overdueOut = outbound.filter(c => c.urgency === 'red');
    const overdueIn  = inbound.filter(c  => c.urgency === 'red');
    const todayOut   = outbound.filter(c => c.dueLabel === 'Today');
    const todayIn    = inbound.filter(c  => c.dueLabel === 'Today');
    return {
      totalOut:  outbound.length,
      totalIn:   inbound.length,
      overdueOut: overdueOut.length,
      overdueIn:  overdueIn.length,
      todayOut:   todayOut.length,
      todayIn:    todayIn.length,
      firstOverduePerson: overdueOut[0]?.personNickname ?? overdueOut[0]?.personName,
      firstTodayPerson:   todayOut[0]?.personNickname   ?? todayOut[0]?.personName,
      firstInPerson:      inbound[0]?.personNickname    ?? inbound[0]?.personName,
    };
  }, [commitments]);

  const message = React.useMemo(
    () => pickTemplate(state, seedRef.current),
    [state],
  );

  const hasOverdue = state.overdueOut > 0;

  if (isLoading) {
    return (
      <div style={{ marginTop: 36 }}>
        <TrumpetSkeleton width="88%" height={22} />
        <TrumpetSkeleton width="72%" height={22} style={{ marginTop: 10 }} />
        <TrumpetSkeleton width="55%" height={22} style={{ marginTop: 10 }} />
      </div>
    );
  }

  return (
    <div style={{ marginTop: 36 }}>
      {/* Message card */}
      <div style={{
        borderRadius: 20,
        padding:      '26px 28px',
        background:   hasOverdue
          ? 'linear-gradient(135deg, rgba(30,22,18,0.9), rgba(26,18,14,0.95))'
          : 'linear-gradient(135deg, var(--trumpet-surface), var(--trumpet-surface))',
        border:       hasOverdue
          ? '1px solid rgba(255,200,80,0.14)'
          : '1px solid var(--trumpet-edge)',
        boxShadow:    '0 18px 40px -20px rgba(0,0,0,0.6)',
      }}>
        {/* Trumpet label */}
        <div style={{
          display:    'flex',
          alignItems: 'center',
          gap:         8,
          marginBottom: 16,
        }}>
          <div style={{
            width:          30,
            height:         30,
            borderRadius:   '50%',
            background:     hasOverdue ? 'rgba(243,178,35,0.15)' : 'var(--trumpet-chip-bg)',
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'center',
            flexShrink:     0,
            color:          hasOverdue ? '#f3c870' : 'rgba(243,239,230,0.6)',
          }}>
            <HandshakeIcon size={16} />
          </div>
          <span style={{
            fontSize:      13,
            fontWeight:    700,
            letterSpacing: 1.1,
            color:         hasOverdue ? 'rgba(243,178,35,0.8)' : 'var(--trumpet-chip-fg)',
            textTransform: 'uppercase',
            fontFamily:    tokens.font,
          }}>
            Trumpet
          </span>
        </div>

        {/* Message */}
        <p style={{
          margin:        0,
          fontSize:      22,
          fontWeight:    500,
          lineHeight:    1.55,
          color:         hasOverdue ? '#e8ddd0' : tokens.fg,
          fontFamily:    tokens.font,
          letterSpacing: -0.2,
        }}>
          {message}
        </p>

        {/* Inline stat chips */}
        <div style={{ display: 'flex', gap: 10, marginTop: 22, flexWrap: 'wrap' }}>
          {state.totalOut > 0 && (
            <StatChip label={`${state.totalOut} outbound`} dot="rgba(243,239,230,0.5)" />
          )}
          {state.totalIn > 0 && (
            <StatChip label={`${state.totalIn} inbound`} dot="rgba(243,239,230,0.35)" />
          )}
          {state.overdueOut > 0 && (
            <StatChip label={`${state.overdueOut} overdue`} dot={tokens.amber} highlight />
          )}
          {state.todayOut > 0 && (
            <StatChip label={`${state.todayOut} due today`} dot={tokens.green} />
          )}
        </div>
      </div>
    </div>
  );
}

function StatChip({ label, dot, highlight }: { label: string; dot: string; highlight?: boolean }) {
  return (
    <div style={{
      display:      'flex',
      alignItems:   'center',
      gap:           6,
      padding:      '6px 12px',
      borderRadius: 99,
      background:   highlight ? 'rgba(243,178,35,0.1)' : 'var(--trumpet-chip-bg)',
      border:       highlight ? '1px solid rgba(243,178,35,0.22)' : '1px solid var(--trumpet-edge)',
    }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, display: 'inline-block', flexShrink: 0 }} />
      <span style={{ fontSize: 14, fontWeight: 600, color: highlight ? '#f3c870' : 'var(--trumpet-chip-fg)', fontFamily: tokens.font }}>
        {label}
      </span>
    </div>
  );
}

// ── Avatar ────────────────────────────────────────────────────────────────────

const GRADIENTS = [
  'linear-gradient(135deg, #6366f1, #8b5cf6)',
  'linear-gradient(135deg, #f59e0b, #ef4444)',
  'linear-gradient(135deg, #10b981, #3b82f6)',
  'linear-gradient(135deg, #ec4899, #8b5cf6)',
  'linear-gradient(135deg, #f97316, #eab308)',
  'linear-gradient(135deg, #06b6d4, #6366f1)',
];

function nameGradient(name?: string): string {
  if (!name) return GRADIENTS[0];
  const hash = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  return GRADIENTS[hash % GRADIENTS.length];
}

function PersonAvatar({ photoUrl, name, size = 28 }: { photoUrl?: string; name?: string; size?: number }) {
  const [err, setErr] = React.useState(false);
  const showImg = photoUrl && !err;
  const initial = name ? name.charAt(0).toUpperCase() : '?';
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      overflow: 'hidden',
      background: showImg ? 'transparent' : nameGradient(name),
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      border: '1.5px solid rgba(0,0,0,0.12)',
    }}>
      {showImg
        ? <img src={photoUrl} alt={name} onError={() => setErr(true)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        : <span style={{ fontSize: size * 0.42, fontWeight: 700, color: 'rgba(255,255,255,0.92)', fontFamily: tokens.font, lineHeight: 1 }}>{initial}</span>
      }
    </div>
  );
}

// ── Group header ──────────────────────────────────────────────────────────────

function GroupHeader({ label, color }: { label: string; color: string }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, letterSpacing: 1.3, textTransform: 'uppercase',
      color, fontFamily: tokens.font,
      padding: '18px 0 8px',
    }}>
      {label}
    </div>
  );
}

// ── Commitment row ────────────────────────────────────────────────────────────

const DOT: Record<'green' | 'amber' | 'red', string> = {
  green: tokens.green, amber: tokens.amber, red: tokens.red,
};

function CommitRow({
  commitment, completing, onComplete,
}: {
  commitment: Commitment;
  completing: boolean;
  onComplete: () => void;
}) {
  const [rowHover,    setRowHover]    = React.useState(false);
  const [iconHover,   setIconHover]   = React.useState(false);
  const [showPoked,   setShowPoked]   = React.useState(false);
  const [showModal,   setShowModal]   = React.useState(false);
  const [personEmail, setPersonEmail] = React.useState<string | null>(null); // null = not yet loaded

  const displayName = commitment.personNickname ?? commitment.personName;
  const hasPerson   = Boolean(commitment.person_id);

  const handlePokeClose = React.useCallback(() => {
    setShowModal(false);
  }, []);

  // Fetch email lazily when poke icon is clicked (only once)
  const openModal = React.useCallback(async () => {
    if (personEmail === null && commitment.person_id) {
      try {
        const resp = await client.records.get(TABLES.people, commitment.person_id);
        const data = (resp as any).data ?? resp;
        setPersonEmail((data?.email ?? '').trim());
      } catch {
        setPersonEmail('');
      }
    }
    setShowModal(true);
  }, [personEmail, commitment.person_id]);

  return (
    <>
      <div style={{
        borderBottom: '1px solid rgba(0,0,0,0.08)',
        opacity: completing ? 0.4 : 1,
        transition: 'opacity 0.2s',
      }}>
        <div
          style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 2px' }}
          onMouseEnter={() => setRowHover(true)}
          onMouseLeave={() => setRowHover(false)}
        >
          {/* Urgency dot */}
          <div style={{
            width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
            background: DOT[commitment.urgency],
          }} />

          {/* Title + person */}
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden', minWidth: 0 }}>
            <span style={{
              fontSize: 21, fontWeight: 700, color: tokens.ink,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              fontFamily: tokens.font, flexShrink: 1,
            }}>
              {commitment.title}
            </span>
            {displayName && (
              <>
                <span style={{ color: 'rgba(0,0,0,0.28)', fontSize: 15, flexShrink: 0 }}>
                  {commitment.type === 'to_others' ? '→' : '←'}
                </span>
                <PersonAvatar photoUrl={commitment.personPhotoUrl} name={commitment.personName} size={24} />
                <span style={{ fontSize: 18, fontWeight: 500, color: tokens.inkSoft, whiteSpace: 'nowrap', fontFamily: tokens.font, flexShrink: 0 }}>
                  {displayName}
                </span>
              </>
            )}
          </div>

          {/* Due label */}
          <span style={{
            fontSize: 18, fontWeight: 700,
            color: commitment.urgency === 'red' ? tokens.red : tokens.inkSoft,
            flexShrink: 0, fontFamily: tokens.font,
          }}>
            {commitment.dueLabel}
          </span>

          {/* Poke button — always visible when person linked */}
          {hasPerson && !showPoked && (
            <button
              onClick={openModal}
              onMouseEnter={() => setIconHover(true)}
              onMouseLeave={() => setIconHover(false)}
              title="Poke"
              style={{
                width: 30, height: 30,
                borderRadius: 8,
                border: `1.5px solid ${iconHover ? 'rgba(0,0,0,0.35)' : 'rgba(0,0,0,0.18)'}`,
                background:   iconHover ? 'rgba(0,0,0,0.08)' : 'rgba(0,0,0,0.04)',
                cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0,
                transform:  iconHover ? 'scale(1.15) rotate(-8deg)' : 'scale(1) rotate(0deg)',
                transition: 'transform 0.18s cubic-bezier(0.34,1.56,0.64,1), border-color 0.15s, background 0.15s',
                boxShadow:  iconHover ? '0 0 8px rgba(0,0,0,0.15)' : 'none',
              }}
            >
              <PokeIcon size={15} color={iconHover ? tokens.ink : tokens.inkSoft} />
            </button>
          )}

          {/* Poked badge */}
          {showPoked && (
            <span style={{
              fontSize: 12, fontWeight: 700, color: tokens.amber,
              background: `${tokens.amber}18`,
              border: `1px solid ${tokens.amber}44`,
              borderRadius: 6, padding: '2px 8px', flexShrink: 0, fontFamily: tokens.font,
            }}>
              poked
            </span>
          )}

          {/* Complete button */}
          <button
            onClick={onComplete}
            disabled={completing}
            title="Mark done"
            style={{
              width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
              border: `2px solid ${rowHover ? tokens.green : 'rgba(0,0,0,0.22)'}`,
              background: rowHover ? tokens.green : 'transparent',
              cursor: completing ? 'default' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'border-color 0.15s, background 0.15s',
            }}
          >
            {rowHover && <span style={{ color: '#fff', fontSize: 13, fontWeight: 800 }}>✓</span>}
          </button>
        </div>
      </div>

      {/* Poke modal */}
      {showModal && (
        <PokeModal
          commitment={commitment}
          personEmail={personEmail ?? ''}
          onClose={handlePokeClose}
          onSuccess={() => setShowPoked(true)}
        />
      )}
    </>
  );
}

// ── Grouped list ──────────────────────────────────────────────────────────────

function GroupedList({
  items, completing, onComplete, emptyMsg,
}: {
  items:      Commitment[];
  completing: Set<string>;
  onComplete: (id: string) => void;
  emptyMsg:   string;
}) {
  if (items.length === 0) {
    return (
      <div style={{ padding: '28px 4px', fontSize: 18, color: tokens.inkSoft, fontFamily: tokens.font }}>
        {emptyMsg}
      </div>
    );
  }

  const overdue  = items.filter(c => c.urgency === 'red');
  const today    = items.filter(c => c.urgency === 'green');
  const upcoming = items.filter(c => c.urgency === 'amber');

  return (
    <div>
      {overdue.length > 0 && (
        <>
          <GroupHeader label={`Overdue · ${overdue.length}`} color={tokens.red} />
          {overdue.map(c => (
            <CommitRow key={c.id} commitment={c} completing={completing.has(c.id)} onComplete={() => onComplete(c.id)} />
          ))}
        </>
      )}
      {today.length > 0 && (
        <>
          <GroupHeader label={`Today · ${today.length}`} color={tokens.green} />
          {today.map(c => (
            <CommitRow key={c.id} commitment={c} completing={completing.has(c.id)} onComplete={() => onComplete(c.id)} />
          ))}
        </>
      )}
      {upcoming.length > 0 && (
        <>
          <GroupHeader label={`Upcoming · ${upcoming.length}`} color={tokens.amber} />
          {upcoming.map(c => (
            <CommitRow key={c.id} commitment={c} completing={completing.has(c.id)} onComplete={() => onComplete(c.id)} />
          ))}
        </>
      )}
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function CommitsSkeleton() {
  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {[1, 2, 3, 4, 5].map(i => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0' }}>
          <TrumpetSkeleton width={10} height={10} radius={5} />
          <TrumpetSkeleton width="42%" height={20} />
          <TrumpetSkeleton width={24} height={24} radius={12} style={{ marginLeft: 4 }} />
          <TrumpetSkeleton width={60} height={18} />
          <TrumpetSkeleton width={55} height={18} style={{ marginLeft: 'auto' }} />
          <TrumpetSkeleton width={26} height={26} radius={13} />
        </div>
      ))}
    </div>
  );
}

// ── Tab button ────────────────────────────────────────────────────────────────

function TabBtn({
  label, active, onClick, icon,
}: {
  label: string; active: boolean; onClick: () => void;
  icon: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 20, fontWeight: 600,
        color: active ? tokens.ink : '#908aa0',
        paddingBottom: 12, cursor: 'pointer',
        position: 'relative', background: 'none', border: 'none',
        transition: 'color 0.15s', fontFamily: tokens.font,
        display: 'flex', alignItems: 'center', gap: 7,
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', opacity: active ? 1 : 0.5, transition: 'opacity 0.15s' }}>
        {icon}
      </span>
      {label}
      {active && (
        <span style={{
          position: 'absolute', left: 0, right: 0, bottom: -1,
          height: 2.5, background: tokens.ink, borderRadius: 2,
        }} />
      )}
    </button>
  );
}

// ── Main tab ──────────────────────────────────────────────────────────────────

type CommitView = 'mine' | 'yours';

export function CommitsTab() {
  const commitments = useCommitments();
  const { outbound, inbound, isLoading, refresh } = commitments;
  const [view, setView] = React.useState<CommitView>('mine');
  const [completing, setCompleting] = React.useState<Set<string>>(new Set());
  const headingIconRef = React.useRef<HTMLDivElement>(null);

  // Animate heading icon color when switching tabs
  React.useEffect(() => {
    if (!headingIconRef.current) return;
    gsap.to(headingIconRef.current, {
      color:    TAB_ICON_COLOR[view],
      duration: 0.4,
      ease:     'power2.out',
    });
  }, [view]);

  const handleComplete = async (id: string) => {
    setCompleting(prev => new Set(prev).add(id));
    await completeCommitment(id);
    refresh();
    setCompleting(prev => { const n = new Set(prev); n.delete(id); return n; });
  };

  return (
    <>
      {/* ── Left column ── */}
      <div style={{ position: 'absolute', left: 70, top: 118, width: 650 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div ref={headingIconRef} style={{ color: TAB_ICON_COLOR.mine, flexShrink: 0, marginTop: 2 }}>
            <HandshakeIcon size={42} />
          </div>
          <div style={{ fontSize: 52, fontWeight: 800, letterSpacing: -2, color: tokens.fg, fontFamily: tokens.font, lineHeight: 1 }}>
            Commitments
          </div>
        </div>
        <div style={{ fontSize: 19, color: tokens.muted, fontFamily: tokens.font, marginTop: 8, fontWeight: 500 }}>
          What you owe, and what's owed to you.
        </div>

        <TrumpetVoiceWidget commitments={commitments} isLoading={isLoading} />
      </div>

      {/* ── Right column ── */}
      <div style={{ position: 'absolute', right: 60, top: 118, width: 620 }}>
        <div style={{
          borderRadius: 26,
          background: tokens.lilac,
          boxShadow: '0 1px 0 rgba(255,255,255,0.45) inset, 0 30px 60px -30px rgba(0,0,0,0.6)',
          overflow: 'hidden',
        }}>
          {/* Card header */}
          <div style={{ padding: '26px 30px 0' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <span style={{ fontSize: 26, fontWeight: 700, color: tokens.ink, fontFamily: tokens.font, letterSpacing: -0.4 }}>
                All commitments
              </span>
              <button
                onClick={refresh}
                title="Refresh"
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  padding: '4px 6px', borderRadius: 8, color: tokens.inkSoft,
                  display: 'flex', alignItems: 'center',
                }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M23 4v6h-6" /><path d="M1 20v-6h6" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
              </button>
            </div>

            <div style={{ display: 'flex', gap: 26, position: 'relative' }}>
              <TabBtn
                icon={<HandshakeIcon size={18} />}
                label={`From your end${!isLoading ? ` (${outbound.length})` : ''}`}
                active={view === 'mine'}
                onClick={() => setView('mine')}
              />
              <TabBtn
                icon={<CrossedFingersIcon size={18} />}
                label={`To you${!isLoading ? ` (${inbound.length})` : ''}`}
                active={view === 'yours'}
                onClick={() => setView('yours')}
              />
            </div>
            <div style={{ height: 1.5, background: 'rgba(0,0,0,0.1)', marginTop: -1.5 }} />
          </div>

          {/* Scrollable list */}
          <div style={{
            padding: '4px 30px 28px',
            maxHeight: 630,
            overflowY: 'auto',
            scrollbarWidth: 'none',
          }}>
            {isLoading && <CommitsSkeleton />}

            {!isLoading && view === 'mine' && (
              <GroupedList
                items={outbound}
                completing={completing}
                onComplete={handleComplete}
                emptyMsg="No active outbound commitments — all clear! ✓"
              />
            )}

            {!isLoading && view === 'yours' && (
              <GroupedList
                items={inbound}
                completing={completing}
                onComplete={handleComplete}
                emptyMsg="Nothing owed to you right now."
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
}
