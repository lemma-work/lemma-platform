/**
 * CommitmentsCard — home-screen slice: today + overdue commitments.
 * Two tabs: "From your end" (outbound) | "To you" (inbound).
 * Each row shows an inline circular avatar + person name.
 * Checking a row marks it completed via the Lemma records API.
 */
import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { TrumpetSkeleton } from '../shared/TrumpetSkeleton';
import type { Commitment } from '@/hooks/useCommitments';
import { completeCommitment } from '@/hooks/useCommitments';

type CommitTab = 'mine' | 'yours';

interface Props {
  outbound:  Commitment[];
  inbound:   Commitment[];
  isLoading: boolean;
  onRefresh: () => void;
  onViewAll?: () => void;
}

const DOT_COLORS: Record<'green' | 'amber' | 'red', string> = {
  green: tokens.green,
  amber: tokens.amber,
  red:   tokens.red,
};

/** Deterministic gradient from a name string */
function nameGradient(name?: string): string {
  const gradients = [
    'linear-gradient(135deg, #6366f1, #8b5cf6)',
    'linear-gradient(135deg, #f59e0b, #ef4444)',
    'linear-gradient(135deg, #10b981, #3b82f6)',
    'linear-gradient(135deg, #ec4899, #8b5cf6)',
    'linear-gradient(135deg, #f97316, #eab308)',
    'linear-gradient(135deg, #06b6d4, #6366f1)',
  ];
  if (!name) return gradients[0];
  const hash = name.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0);
  return gradients[hash % gradients.length];
}

/** Small circular avatar — photo or gradient fallback */
function PersonAvatar({ photoUrl, name, size = 28 }: { photoUrl?: string; name?: string; size?: number }) {
  const [imgError, setImgError] = React.useState(false);
  const showImg = photoUrl && !imgError;
  const initials = name ? name.charAt(0).toUpperCase() : '?';

  return (
    <div style={{
      width:          size,
      height:         size,
      borderRadius:   '50%',
      flexShrink:     0,
      overflow:       'hidden',
      background:     showImg ? 'transparent' : nameGradient(name),
      display:        'flex',
      alignItems:     'center',
      justifyContent: 'center',
      border:         '1.5px solid rgba(0,0,0,0.12)',
    }}>
      {showImg ? (
        <img
          src={photoUrl}
          alt={name ?? 'person'}
          onError={() => setImgError(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      ) : (
        <span style={{
          fontSize:   size * 0.42,
          fontWeight: 700,
          color:      'rgba(255,255,255,0.92)',
          fontFamily: tokens.font,
          lineHeight: 1,
        }}>
          {initials}
        </span>
      )}
    </div>
  );
}

export function CommitmentsCard({ outbound, inbound, isLoading, onRefresh, onViewAll }: Props) {
  const [tab, setTab]   = React.useState<CommitTab>('mine');
  const [completing, setCompleting] = React.useState<Set<string>>(new Set());

  const HOME_CAP = 4;
  const allItems = tab === 'mine' ? outbound : inbound;
  const items    = allItems.slice(0, HOME_CAP);
  const overflow = Math.max(0, allItems.length - HOME_CAP);

  const handleComplete = async (id: string) => {
    setCompleting(prev => new Set(prev).add(id));
    await completeCommitment(id);
    onRefresh();
    setCompleting(prev => { const n = new Set(prev); n.delete(id); return n; });
  };

  return (
    <div style={{
      borderRadius: 26,
      padding:      '30px 32px 22px',
      background:   tokens.lilac,
      marginTop:    24,
      boxShadow:    'var(--trumpet-card-shadow)',
      color:        tokens.ink,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 28, fontWeight: 700, letterSpacing: -0.4, color: tokens.ink, fontFamily: tokens.font }}>
          Commitments
        </span>
        <button
          onClick={onViewAll}
          style={{
            display:    'inline-flex', alignItems: 'center', gap: 4,
            fontSize:   19, fontWeight: 600, color: '#4f4a42',
            background: 'none', border: 'none', cursor: 'pointer',
            padding:    '4px 6px', borderRadius: 8, fontFamily: tokens.font,
          }}
        >
          View all
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 6 15 12 9 18" />
          </svg>
        </button>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 26, marginTop: 14, position: 'relative' }}>
        <TabBtn label="From your end" active={tab === 'mine'}  onClick={() => setTab('mine')}  />
        <TabBtn label="To you"        active={tab === 'yours'} onClick={() => setTab('yours')} />
      </div>
      <div style={{ height: 1.5, background: 'rgba(0,0,0,0.1)', marginTop: -1.5 }} />

      {/* Rows */}
      <div style={{ marginTop: 6 }}>
        {isLoading && <CommitSkeleton />}

        {!isLoading && items.length === 0 && (
          <div style={{
            padding:    '20px 2px',
            fontSize:   19,
            color:      tokens.inkSoft,
            fontFamily: tokens.font,
          }}>
            {tab === 'mine'
              ? 'No active commitments from your end — all clear! ✓'
              : 'Nothing owed to you right now.'}
          </div>
        )}

        {!isLoading && items.map(c => (
          <CommitRow
            key={c.id}
            commitment={c}
            completing={completing.has(c.id)}
            onComplete={() => handleComplete(c.id)}
          />
        ))}
        {!isLoading && overflow > 0 && (
          <button
            onClick={onViewAll}
            style={{
              marginTop:  8,
              display:    'flex',
              alignItems: 'center',
              gap:         6,
              fontSize:   17,
              fontWeight: 600,
              color:      tokens.inkSoft,
              background: 'none',
              border:     'none',
              cursor:     'pointer',
              padding:    '6px 2px',
              fontFamily: tokens.font,
            }}
          >
            +{overflow} more
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 6 15 12 9 18" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────

function TabBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize:      21,
        fontWeight:    600,
        color:         active ? tokens.ink : '#908aa0',
        paddingBottom: 12,
        cursor:        'pointer',
        position:      'relative',
        background:    'none',
        border:        'none',
        transition:    'color 0.15s',
        fontFamily:    tokens.font,
      }}
    >
      {label}
      {active && (
        <span style={{
          position:     'absolute',
          left:          0,
          right:         0,
          bottom:       -1,
          height:        2.5,
          background:   tokens.ink,
          borderRadius: 2,
        }} />
      )}
    </button>
  );
}

function CommitRow({
  commitment,
  completing,
  onComplete,
}: {
  commitment: Commitment;
  completing: boolean;
  onComplete: () => void;
}) {
  const [hover, setHover] = React.useState(false);
  const hasParty = commitment.personName || commitment.personNickname;
  const displayName = commitment.personNickname ?? commitment.personName;

  return (
    <div
      style={{
        display:      'flex',
        alignItems:   'center',
        gap:           12,
        padding:      '14px 2px',
        borderBottom: '1px solid rgba(0,0,0,0.08)',
        opacity:      completing ? 0.45 : 1,
        transition:   'opacity 0.2s',
      }}
    >
      {/* Status dot */}
      <div style={{
        width:        11,
        height:       11,
        borderRadius: '50%',
        flexShrink:   0,
        background:   DOT_COLORS[commitment.urgency],
      }} />

      {/* Title + person inline */}
      <div style={{
        flex:       1,
        display:    'flex',
        alignItems: 'center',
        gap:        8,
        overflow:   'hidden',
        minWidth:   0,
      }}>
        <span style={{
          fontSize:     22,
          fontWeight:   700,
          color:        tokens.ink,
          overflow:     'hidden',
          textOverflow: 'ellipsis',
          whiteSpace:   'nowrap',
          fontFamily:   tokens.font,
          flexShrink:   1,
        }}>
          {commitment.title}
        </span>

        {hasParty && (
          <>
            {/* Soft separator arrow */}
            <span style={{ color: 'rgba(0,0,0,0.3)', fontSize: 16, flexShrink: 0 }}>
              {commitment.type === 'to_others' ? '→' : '←'}
            </span>
            {/* Avatar */}
            <PersonAvatar
              photoUrl={commitment.personPhotoUrl}
              name={commitment.personName}
              size={26}
            />
            {/* Name */}
            <span style={{
              fontSize:    19,
              fontWeight:  500,
              color:       tokens.inkSoft,
              whiteSpace:  'nowrap',
              fontFamily:  tokens.font,
              flexShrink:  0,
            }}>
              {displayName}
            </span>
          </>
        )}
      </div>

      {/* Due label */}
      <span style={{
        fontSize:   20,
        fontWeight: 700,
        color:      commitment.urgency === 'red' ? tokens.accent : '#2a2722',
        flexShrink: 0,
        fontFamily: tokens.font,
      }}>
        {commitment.dueLabel}
      </span>

      {/* Done button */}
      <button
        onClick={onComplete}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        title="Mark done"
        disabled={completing}
        style={{
          width:          26,
          height:         26,
          borderRadius:   '50%',
          border:         `2px solid ${hover ? tokens.green : 'rgba(0,0,0,0.25)'}`,
          background:     hover ? tokens.green : 'transparent',
          cursor:         completing ? 'default' : 'pointer',
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          flexShrink:     0,
          transition:     'border-color 0.15s, background 0.15s',
        }}
      >
        {hover && <span style={{ color: '#fff', fontSize: 13, fontWeight: 800 }}>✓</span>}
      </button>
    </div>
  );
}

function CommitSkeleton() {
  return (
    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '6px 0' }}>
          <TrumpetSkeleton width={11} height={11} radius={6} />
          <TrumpetSkeleton width="45%" height={20} />
          <TrumpetSkeleton width={26} height={26} radius={13} style={{ marginLeft: 4 }} />
          <TrumpetSkeleton width={60} height={18} />
          <TrumpetSkeleton width={50} height={20} style={{ marginLeft: 'auto' }} />
        </div>
      ))}
    </div>
  );
}
