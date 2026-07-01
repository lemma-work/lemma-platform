/**
 * HomeTab — two-column layout matching the Trumpet Home.html prototype.
 * Left: BigDate + greeting + TrumpetSummary
 * Right: ScheduleCard + CommitmentsCard
 */
import * as React from 'react';
import { BigDate }          from './BigDate';
import { TrumpetSummary }       from './TrumpetSummary';
import { ScheduleCard }     from './ScheduleCard';
import { CommitmentsCard }  from './CommitmentsCard';
import { useProfile }       from '@/hooks/useProfile';
import { useCommitments }   from '@/hooks/useCommitments';
import { useSchedule }      from '@/hooks/useSchedule';
import { useTrumpetSummary }    from '@/hooks/useTrumpetSummary';
import { useTeamHabitInbox } from '@/hooks/useTeamHabitInbox';
import type { Tab }         from '../layout/Dock';
import { CalendarMascot }   from '../mascot/CalendarMascot';
import { tokens }           from '@/lib/tokens';

interface Props {
  onNavigate: (tab: Tab) => void;
}

function TeamHabitNudge({ count, onReview }: { count: number; onReview: () => void }) {
  const [hover, setHover] = React.useState(false);

  return (
    <div style={{
      display:      'flex',
      alignItems:   'center',
      gap:           12,
      padding:      '12px 16px',
      borderRadius:  14,
      background:   'rgba(140,100,220,0.08)',
      border:       '1.5px solid rgba(140,100,220,0.2)',
      marginBottom:  12,
    }}>
      <span style={{ fontSize: 22, flexShrink: 0 }}>📣</span>
      <div style={{ flex: 1 }}>
        <div style={{
          fontSize:   15,
          fontWeight: 700,
          color:      tokens.fg,
          fontFamily: tokens.font,
          marginBottom: 2,
        }}>
          {count} team habit{count !== 1 ? 's' : ''} recommended for you
        </div>
        <div style={{ fontSize: 13, color: tokens.muted, fontFamily: tokens.font }}>
          Your admin has suggested habits for the team. You decide to add them.
        </div>
      </div>
      <button
        onClick={onReview}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          padding:     '7px 14px',
          borderRadius: 8,
          border:      'none',
          cursor:      'pointer',
          fontFamily:   tokens.font,
          fontSize:     13,
          fontWeight:   700,
          background:   hover ? '#7a5aaa' : '#8a6abf',
          color:       '#fff',
          transition:  'background 0.15s',
          flexShrink:   0,
        }}
      >
        Review →
      </button>
    </div>
  );
}

export function HomeTab({ onNavigate }: Props) {
  const { firstName }                                        = useProfile();
  const { outbound, inbound, isLoading: commLoading, refresh } = useCommitments();
  const { status, items, gcalConnected }                     = useSchedule();
  const { pendingCount }                                     = useTeamHabitInbox();

  // Summary uses all active commitments + today's schedule items
  const summary = useTrumpetSummary(firstName, outbound, inbound, items, gcalConnected);

  return (
    <>
      {/* ── Left column ── */}
      <div style={{
        position: 'absolute',
        left:     70,
        top:      118,
        width:    720,
      }}>
        <BigDate />
        <TrumpetSummary data={summary} firstName={firstName} userPhoto="/photos/user.jpg" />
      </div>

      {/* ── Right column ── */}
      <div style={{
        position:       'absolute',
        right:          60,
        top:            122,
        width:          600,
        maxHeight:      760,
        overflowY:      'auto',
        scrollbarWidth: 'none',
      }}>
        {/* Team habit nudge banner */}
        {pendingCount > 0 && (
          <TeamHabitNudge count={pendingCount} onReview={() => onNavigate('schedule')} />
        )}

        <ScheduleCard
          status={status}
          items={items}
          gcalConnected={gcalConnected}
          onViewAll={() => onNavigate('schedule')}
        />

        <CommitmentsCard
          outbound={outbound}
          inbound={inbound}
          isLoading={commLoading}
          onRefresh={refresh}
          onViewAll={() => onNavigate('commits')}
        />
      </div>

      <CalendarMascot
        status={status}
        items={items}
      />
    </>
  );
}
