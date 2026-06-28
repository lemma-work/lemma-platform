/**
 * ScheduleTab — full schedule view.
 *
 * Left:  Day context + schedule summary stats + "next up" block
 * Right: Tabbed card — Today (full event list) | Habits (all habits + daily check-off)
 */
import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { useSchedule } from '@/hooks/useSchedule';
import { isEventActive, todayISO } from '@/lib/time';
import { TrumpetSkeleton } from '../shared/TrumpetSkeleton';
import type { ScheduleItem } from '@/hooks/useSchedule';
import { formatDateStamp, currentDateNum } from '@/lib/time';
import { initSimData, getTeamLeaderboard } from '@/lib/sim-data';
import type { SimTeamHabit } from '@/lib/sim-data';
import { useHabitStreak, computeStreak } from '@/hooks/useHabitStreak';
import { useTeamHabitInbox } from '@/hooks/useTeamHabitInbox';

// ─── Left column ──────────────────────────────────────────────────────────────

function ScheduleDateBlock() {
  const { line1, line2 } = formatDateStamp();
  const dateNum = currentDateNum();

  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20 }}>
      <div style={{
        fontSize:      120,
        fontWeight:    800,
        letterSpacing: -6,
        lineHeight:    0.88,
        color:         tokens.fg,
        fontFamily:    tokens.font,
      }}>
        {dateNum}
      </div>
      <div style={{ paddingBottom: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: 28, fontWeight: 700, color: tokens.fg,   fontFamily: tokens.font }}>{line2}</span>
        <span style={{ fontSize: 22, fontWeight: 500, color: tokens.muted, fontFamily: tokens.font }}>{line1}</span>
      </div>
    </div>
  );
}

/** Compute summary stats from schedule items */
function useScheduleStats(items: ScheduleItem[]) {
  return React.useMemo(() => {
    const events = items.filter(i => !i.isHabit);
    const habits = items.filter(i => i.isHabit);

    // Total meeting/event minutes
    let totalMins = 0;
    for (const e of events) {
      totalMins += estimateDuration(e.start_time, e.end_time);
    }
    const hoursLabel = totalMins >= 60
      ? `${(totalMins / 60).toFixed(1).replace('.0', '')}h`
      : `${totalMins}m`;

    // Next event (first upcoming, not done)
    const now = new Date();
    const upcoming = events.find(e => {
      const t = parseAmPm(e.start_time);
      return t > now.getHours() * 60 + now.getMinutes();
    });

    // Current event
    const current = events.find(e => isEventActive(e.start_time, e.end_time));

    return { events, habits, totalMins, hoursLabel, upcoming, current };
  }, [items]);
}

function estimateDuration(start: string, end: string): number {
  if (!end) return 45;
  const a = parseAmPm(start), b = parseAmPm(end);
  return Math.max(0, b - a);
}

function parseAmPm(t: string): number {
  if (!t) return 9999;
  const m = t.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!m) return 9999;
  let h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const p = m[3].toUpperCase();
  if (p === 'PM' && h !== 12) h += 12;
  if (p === 'AM' && h === 12) h = 0;
  return h * 60 + min;
}

function StatPill({ emoji, value, label }: { emoji: string; value: string | number; label: string }) {
  return (
    <div style={{
      display:      'flex',
      alignItems:   'center',
      gap:           10,
      padding:      '14px 18px',
      background:   'var(--trumpet-surface)',
      borderRadius: 16,
      border:       '1px solid var(--trumpet-divider)',
    }}>
      <span style={{
        fontSize:   28,
        lineHeight: 1,
        fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
      }}>{emoji}</span>
      <div>
        <div style={{ fontSize: 26, fontWeight: 700, color: tokens.fg, fontFamily: tokens.font, lineHeight: 1.1 }}>
          {value}
        </div>
        <div style={{ fontSize: 16, color: tokens.muted, fontFamily: tokens.font, marginTop: 2 }}>
          {label}
        </div>
      </div>
    </div>
  );
}

function NextUpBlock({ item }: { item: ScheduleItem }) {
  return (
    <div style={{
      marginTop:    28,
      padding:      '18px 22px',
      borderRadius: 18,
      background:   'var(--trumpet-surface)',
      border:       '1px solid var(--trumpet-edge)',
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: 1.2, color: tokens.muted, textTransform: 'uppercase', fontFamily: tokens.font, marginBottom: 10 }}>
        Next up
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 30, fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif" }}>
          {item.emoji}
        </span>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, color: tokens.fg, fontFamily: tokens.font }}>{item.title}</div>
          <div style={{ fontSize: 17, color: tokens.muted, fontFamily: tokens.font, marginTop: 3 }}>
            {item.start_time}{item.end_time ? ` – ${item.end_time}` : ''}
          </div>
        </div>
      </div>
    </div>
  );
}

function CurrentBlock({ item }: { item: ScheduleItem }) {
  return (
    <div style={{
      marginTop:    28,
      padding:      '18px 22px',
      borderRadius: 18,
      background:   '#1a1208',
      border:       '1px solid rgba(255,200,80,0.18)',
      boxShadow:    '0 0 0 1px rgba(255,200,80,0.08)',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10,
      }}>
        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#f3b223', animation: 'pulse 1.6s ease-in-out infinite' }} />
        <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: 1.2, color: '#f3b223', textTransform: 'uppercase', fontFamily: tokens.font }}>
          Happening now
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 30, fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif" }}>
          {item.emoji}
        </span>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, color: tokens.fg, fontFamily: tokens.font }}>{item.title}</div>
          <div style={{ fontSize: 17, color: '#b9a88a', fontFamily: tokens.font, marginTop: 3 }}>
            {item.start_time}{item.end_time ? ` – ${item.end_time}` : ''}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Right column — schedule list ─────────────────────────────────────────────

type ScheduleSubTab = 'all' | 'events' | 'habits';

function FullScheduleList({
  items,
  done,
  onToggle,
}: {
  items:    ScheduleItem[];
  done:     Set<string>;
  onToggle: (id: string) => void;
}) {
  if (items.length === 0) {
    return (
      <div style={{ padding: '28px 4px', fontSize: 18, color: tokens.inkSoft, fontFamily: tokens.font }}>
        No events scheduled for today.
      </div>
    );
  }

  return (
    <div style={{ position: 'relative' }}>
      {/* Vertical rail */}
      <div style={{
        position:   'absolute',
        left:        13,
        top:         26,
        bottom:      26,
        width:       2,
        background: 'rgba(0,0,0,0.12)',
      }} />
      {items.map(item => {
        const isDone   = done.has(item.id);
        const isActive = !isDone && isEventActive(item.start_time, item.end_time);
        return <FullScheduleRow key={item.id} item={item} isDone={isDone} isActive={isActive} onToggle={() => onToggle(item.id)} />;
      })}
    </div>
  );
}

function FullScheduleRow({
  item, isDone, isActive, onToggle,
}: { item: ScheduleItem; isDone: boolean; isActive: boolean; onToggle: () => void }) {
  const [hover, setHover] = React.useState(false);

  const wrap: React.CSSProperties = isActive ? {
    display:             'grid',
    gridTemplateColumns: '28px 1fr',
    gap:                  14,
    padding:             '16px 20px',
    background:          '#18120e',
    borderRadius:         16,
    margin:              '4px -8px',
    boxShadow:           '0 10px 26px -14px rgba(0,0,0,0.7)',
    position:            'relative', zIndex: 1,
  } : {
    display:             'grid',
    gridTemplateColumns: '28px 1fr',
    gap:                  14,
    padding:             '13px 0',
    position:            'relative', zIndex: 1,
  };

  return (
    <div style={wrap}>
      {/* Circle */}
      <div
        onClick={onToggle}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          width: 26, height: 26, borderRadius: '50%', marginTop: 2,
          border: isDone ? `2px solid ${tokens.green}` : isActive ? '2px solid var(--trumpet-active-ring)' : '2px solid rgba(0,0,0,0.28)',
          background: isDone ? tokens.green : isActive ? 'transparent' : tokens.cream,
          cursor: 'pointer', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transform: hover ? 'scale(1.08)' : 'scale(1)',
          transition: 'transform 0.12s',
        }}
      >
        {isDone && <span style={{ color: '#fff', fontSize: 13, fontWeight: 800 }}>✓</span>}
        {!isDone && isActive && <span style={{ width: 10, height: 10, borderRadius: '50%', background: tokens.fg, display: 'block' }} />}
      </div>

      <div>
        {/* Time + badges */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
          <span style={{ fontSize: 18, fontWeight: 600, color: isActive ? '#b9b2a4' : '#6c665c', fontFamily: tokens.font }}>
            {item.start_time}
            {item.end_time && <span style={{ fontWeight: 400, opacity: 0.65 }}> – {item.end_time}</span>}
          </span>
          {item.isHabit && (
            <span style={{
              fontSize: 11, fontWeight: 600, padding: '2px 7px', borderRadius: 99,
              background: isActive ? 'var(--trumpet-sel-bg)' : 'rgba(0,0,0,0.08)',
              color: isActive ? 'var(--trumpet-sel-fg)' : '#8a8178', fontFamily: tokens.font,
            }}>habit</span>
          )}
          {item.source === 'gcal' && (
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#4285F4', display: 'inline-block', flexShrink: 0 }} />
          )}
        </div>
        {/* Title */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 22, fontWeight: 700, letterSpacing: -0.2,
          color: isActive ? '#fff' : tokens.ink,
          textDecoration: isDone ? 'line-through' : 'none',
          opacity: isDone ? 0.5 : 1,
          fontFamily: tokens.font,
        }}>
          <span style={{ fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif", fontSize: 20, flexShrink: 0 }}>
            {item.emoji}
          </span>
          {item.title}
        </div>
        {item.description && (
          <div style={{ fontSize: 17, color: isActive ? '#aaa294' : '#79736a', marginTop: 3, fontFamily: tokens.font }}>
            {item.description}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Habits sub-tab ───────────────────────────────────────────────────────────

// Converts sim team habits into ScheduleItem shape for unified rendering
function toTeamScheduleItems(habits: SimTeamHabit[]): ScheduleItem[] {
  return habits.map(th => ({
    id:         th.id,
    title:      th.title,
    start_time: th.time,
    end_time:   '',
    source:     'db' as const,
    isHabit:    true,
    emoji:      th.emoji,
    scope:      'team'  as const,
    required:   th.required,
    pushedBy:   th.pushedBy,
  }));
}

function HabitSectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div style={{
      display:        'flex',
      alignItems:     'center',
      gap:             8,
      marginTop:       22,
      marginBottom:    10,
      paddingBottom:   8,
      borderBottom:   '1px solid rgba(0,0,0,0.08)',
    }}>
      <span style={{
        fontSize:      12,
        fontWeight:    700,
        letterSpacing: 1.1,
        textTransform: 'uppercase',
        color:         tokens.inkSoft,
        fontFamily:    tokens.font,
      }}>
        {label}
      </span>
      <span style={{
        fontSize:     11,
        fontWeight:   600,
        color:        '#aaa49c',
        fontFamily:   tokens.font,
      }}>
        {count}
      </span>
    </div>
  );
}

// ─── Left column: pending recommendations ────────────────────────────────────

function PendingRecommendations({
  pending,
  onAdd,
  onDismiss,
}: {
  pending:   SimTeamHabit[];
  onAdd:     (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  return (
    <div style={{ marginTop: 28 }}>
      <div style={{
        fontSize:      12,
        fontWeight:    700,
        letterSpacing: 1.2,
        textTransform: 'uppercase',
        color:         tokens.muted,
        fontFamily:    tokens.font,
        marginBottom:  12,
      }}>
        Recommended for you
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {pending.map(th => (
          <PendingRecommendationRow key={th.id} habit={th} onAdd={onAdd} onDismiss={onDismiss} />
        ))}
      </div>
    </div>
  );
}

function PendingRecommendationRow({
  habit,
  onAdd,
  onDismiss,
}: {
  habit:     SimTeamHabit;
  onAdd:     (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const [addHover, setAddHover]         = React.useState(false);
  const [dismissHover, setDismissHover] = React.useState(false);

  return (
    <div style={{
      display:      'flex',
      alignItems:   'center',
      gap:           12,
      padding:      '12px 16px',
      borderRadius:  14,
      background:   'var(--trumpet-surface)',
      border:       '1px solid var(--trumpet-divider)',
    }}>
      <span style={{
        fontSize:   24,
        fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
        flexShrink:  0,
      }}>
        {habit.emoji}
      </span>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize:     17,
          fontWeight:   700,
          color:        tokens.fg,
          fontFamily:   tokens.font,
          marginBottom: 2,
          display:      'flex',
          alignItems:   'center',
          gap:           7,
        }}>
          {habit.title}
          <span style={{
            fontSize:    10,
            fontWeight:  700,
            padding:    '2px 6px',
            borderRadius: 99,
            fontFamily:  tokens.font,
            background:  habit.required ? 'rgba(200,96,58,0.15)' : 'rgba(110,80,180,0.15)',
            color:       habit.required ? '#c8603a' : '#8a6abf',
          }}>
            {habit.required ? 'required' : 'rec'}
          </span>
        </div>
        <div style={{ fontSize: 13, color: tokens.muted, fontFamily: tokens.font }}>
          {habit.time} · {habit.pushedBy}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
        <button
          onClick={() => onAdd(habit.id)}
          onMouseEnter={() => setAddHover(true)}
          onMouseLeave={() => setAddHover(false)}
          style={{
            padding:     '6px 12px',
            borderRadius: 8,
            border:      'none',
            cursor:      'pointer',
            fontFamily:   tokens.font,
            fontSize:     12,
            fontWeight:   700,
            background:   addHover
              ? (habit.required ? '#b85530' : '#7a5aaa')
              : (habit.required ? '#c8603a' : '#8a6abf'),
            color:       '#fff',
            transition:  'background 0.15s',
          }}
        >
          + Add
        </button>
        <button
          onClick={() => onDismiss(habit.id)}
          onMouseEnter={() => setDismissHover(true)}
          onMouseLeave={() => setDismissHover(false)}
          style={{
            width:        28,
            height:       28,
            borderRadius: 8,
            border:      `1px solid rgba(255,255,255,${dismissHover ? '0.15' : '0.08'})`,
            cursor:      'pointer',
            fontFamily:   tokens.font,
            fontSize:     14,
            background:   dismissHover ? 'rgba(255,255,255,0.08)' : 'transparent',
            color:        tokens.muted,
            transition:  'background 0.15s',
            display:     'flex',
            alignItems:  'center',
            justifyContent: 'center',
            lineHeight:   1,
          }}
          title="Dismiss"
        >
          ×
        </button>
      </div>
    </div>
  );
}

// ─── Habits sub-tab — streak hero + habit lists ───────────────────────────────

function StreakHero({
  personalItems,
  teamItems,
  doneTodayIds,
}: {
  personalItems: ScheduleItem[];
  teamItems:     ScheduleItem[];
  doneTodayIds:  Set<string>;
}) {
  // Best current streak across all personal habits
  const personalStreak = React.useMemo(() => {
    if (personalItems.length === 0) return 0;
    return Math.max(...personalItems.map(h => computeStreak(h.id)));
  }, [personalItems, doneTodayIds]); // eslint-disable-line react-hooks/exhaustive-deps

  // Best current streak across added team habits
  const teamStreak = React.useMemo(() => {
    if (teamItems.length === 0) return 0;
    return Math.max(...teamItems.map(h => computeStreak(h.id)));
  }, [teamItems, doneTodayIds]); // eslint-disable-line react-hooks/exhaustive-deps

  // Team rank from leaderboard
  const teamRank = React.useMemo(() => {
    if (teamItems.length === 0) return null;
    const board = getTeamLeaderboard(teamStreak);
    const pos   = board.findIndex(r => r.isMe);
    return pos >= 0 ? pos + 1 : null;
  }, [teamStreak, teamItems.length]);

  // 7-day completion dots — did you tick ANY habit that day?
  const last7 = React.useMemo(() => {
    const allIds = [...personalItems, ...teamItems].map(h => h.id);
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (6 - i));
      const key = `trumpet_sched_done_${d.toISOString().slice(0, 10)}`;
      try {
        const ids: string[] = JSON.parse(localStorage.getItem(key) ?? '[]');
        return ids.some(id => allIds.includes(id));
      } catch { return false; }
    });
  }, [personalItems, teamItems, doneTodayIds]); // eslint-disable-line react-hooks/exhaustive-deps

  const dayLabels = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const rankLabel = teamRank === 1 ? '🥇' : teamRank === 2 ? '🥈' : teamRank === 3 ? '🥉' : null;

  return (
    <div style={{
      display:      'flex',
      gap:           14,
      marginBottom:  20,
      marginTop:     8,
    }}>
      {/* Personal streak block */}
      <div style={{
        flex:          '0 0 auto',
        padding:      '14px 20px',
        borderRadius:  14,
        background:   'rgba(180,90,20,0.07)',
        border:       '1px solid rgba(180,90,20,0.15)',
        textAlign:    'center',
        minWidth:      100,
      }}>
        <div style={{
          fontSize:   42,
          fontWeight: 800,
          color:      '#b86020',
          fontFamily: tokens.font,
          lineHeight: 1,
          letterSpacing: -1,
        }}>
          {personalStreak}
        </div>
        <div style={{
          fontSize:   20,
          marginTop:   2,
          lineHeight:  1,
        }}>🔥</div>
        <div style={{
          fontSize:   12,
          color:      tokens.inkSoft,
          fontFamily: tokens.font,
          marginTop:   6,
          fontWeight:  600,
        }}>
          personal
        </div>
      </div>

      {/* Right block: 7-day dots + team rank */}
      <div style={{
        flex:          1,
        padding:      '14px 18px',
        borderRadius:  14,
        background:   'rgba(0,0,0,0.04)',
        border:       '1px solid rgba(0,0,0,0.08)',
        display:      'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
      }}>
        {/* 7-day dots */}
        <div>
          <div style={{
            fontSize:   11,
            fontWeight: 600,
            color:      tokens.inkSoft,
            fontFamily: tokens.font,
            marginBottom: 8,
          }}>
            Last 7 days
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {last7.map((filled, i) => (
              <div key={i} style={{ textAlign: 'center' }}>
                <div style={{
                  width:        28,
                  height:       28,
                  borderRadius:  8,
                  background:   filled ? tokens.green : 'rgba(0,0,0,0.1)',
                  marginBottom:  4,
                  transition:   'background 0.2s',
                }} />
                <div style={{
                  fontSize:   10,
                  color:      tokens.inkSoft,
                  fontFamily: tokens.font,
                  fontWeight: 600,
                }}>
                  {dayLabels[i]}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Team rank chip */}
        {teamRank !== null && (
          <div style={{
            display:    'flex',
            alignItems: 'center',
            gap:         6,
            marginTop:   10,
          }}>
            <span style={{ fontSize: 16 }}>{rankLabel ?? '🏅'}</span>
            <span style={{
              fontSize:   13,
              fontWeight: 700,
              color:      tokens.ink,
              fontFamily: tokens.font,
            }}>
              #{teamRank} on team
            </span>
            <span style={{
              fontSize:   12,
              color:      tokens.inkSoft,
              fontFamily: tokens.font,
            }}>
              · {teamStreak} day streak
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function HabitsList({
  habits,
  addedTeamHabits,
  doneTodayIds,
  onToggle,
}: {
  habits:          ScheduleItem[];
  addedTeamHabits: SimTeamHabit[];
  doneTodayIds:    Set<string>;
  onToggle:        (id: string) => void;
}) {
  const teamItems     = React.useMemo(() => toTeamScheduleItems(addedTeamHabits), [addedTeamHabits]);
  const personalItems = habits;
  const total         = personalItems.length + teamItems.length;
  const doneCount     =
    personalItems.filter(h => doneTodayIds.has(h.id)).length +
    teamItems.filter(h => doneTodayIds.has(h.id)).length;

  if (total === 0) {
    return (
      <div style={{ padding: '28px 4px', fontSize: 18, color: tokens.inkSoft, fontFamily: tokens.font }}>
        No habits set up yet. Ask Trumpet to add one.
      </div>
    );
  }

  return (
    <div>
      {/* Streak hero */}
      <StreakHero
        personalItems={personalItems}
        teamItems={teamItems}
        doneTodayIds={doneTodayIds}
      />

      {/* Today's progress bar */}
      <div style={{ marginBottom: 4 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: tokens.inkSoft, fontFamily: tokens.font }}>
            Today
          </span>
          <span style={{ fontSize: 15, fontWeight: 700, color: tokens.ink, fontFamily: tokens.font }}>
            {doneCount} / {total}
          </span>
        </div>
        <div style={{ height: 5, borderRadius: 99, background: 'rgba(0,0,0,0.1)', overflow: 'hidden' }}>
          <div style={{
            height:     '100%',
            width:      `${total ? (doneCount / total) * 100 : 0}%`,
            background: tokens.green,
            borderRadius: 99,
            transition: 'width 0.4s ease',
          }} />
        </div>
      </div>

      {/* Personal habits */}
      {personalItems.length > 0 && (
        <>
          <HabitSectionHeader label="My habits" count={personalItems.length} />
          {personalItems.map(h => (
            <HabitRow key={h.id} habit={h} done={doneTodayIds.has(h.id)} onToggle={() => onToggle(h.id)} />
          ))}
        </>
      )}

      {/* Team habits */}
      {teamItems.length > 0 && (
        <>
          <HabitSectionHeader label="Team" count={teamItems.length} />
          {teamItems.map(h => (
            <HabitRow key={h.id} habit={h} done={doneTodayIds.has(h.id)} onToggle={() => onToggle(h.id)} />
          ))}
        </>
      )}
    </div>
  );
}

function HabitRow({ habit, done, onToggle }: { habit: ScheduleItem; done: boolean; onToggle: () => void }) {
  const [hover, setHover] = React.useState(false);
  const streak = useHabitStreak(habit.id, done);
  const isTeam = habit.scope === 'team';

  // Team checkbox uses a purple tint to distinguish from personal
  const checkBorder   = done
    ? `2px solid ${isTeam ? '#7a5aaa' : tokens.green}`
    : isTeam
      ? '2px solid rgba(120,90,170,0.35)'
      : '2px solid rgba(0,0,0,0.22)';
  const checkBg = done ? (isTeam ? '#7a5aaa' : tokens.green) : 'transparent';

  return (
    <div style={{
      display:      'flex',
      alignItems:   'center',
      gap:           14,
      padding:      '13px 0',
      borderBottom: '1px solid rgba(0,0,0,0.07)',
      opacity:       done ? 0.52 : 1,
      transition:   'opacity 0.2s',
    }}>
      {/* Checkbox */}
      <div
        onClick={onToggle}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          width: 26, height: 26, borderRadius: 8, flexShrink: 0,
          border:     checkBorder,
          background: checkBg,
          cursor:    'pointer',
          display:   'flex', alignItems: 'center', justifyContent: 'center',
          transform:  hover ? 'scale(1.08)' : 'scale(1)',
          transition: 'transform 0.12s, background 0.15s',
        }}
      >
        {done && <span style={{ color: '#fff', fontSize: 14, fontWeight: 800 }}>✓</span>}
      </div>

      {/* Emoji */}
      <span style={{
        fontSize:   24,
        fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
        flexShrink: 0,
      }}>
        {habit.emoji}
      </span>

      {/* Title + meta */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize:       21,
          fontWeight:     700,
          color:          tokens.ink,
          fontFamily:     tokens.font,
          textDecoration: done ? 'line-through' : 'none',
          whiteSpace:     'nowrap',
          overflow:       'hidden',
          textOverflow:   'ellipsis',
        }}>
          {habit.title}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
          <span style={{ fontSize: 13, color: '#8a8278', fontFamily: tokens.font }}>
            {habit.start_time}
          </span>
          {isTeam && habit.pushedBy && (
            <span style={{ fontSize: 12, color: '#9a9290', fontFamily: tokens.font }}>
              · {habit.pushedBy}
            </span>
          )}
        </div>
      </div>

      {/* Right side: compliance badge (team) + streak */}
      <div style={{
        display:    'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap:         4,
        flexShrink: 0,
      }}>
        {isTeam && (
          <span style={{
            fontSize:    10,
            fontWeight:  700,
            padding:    '2px 7px',
            borderRadius: 99,
            fontFamily:  tokens.font,
            letterSpacing: 0.3,
            background:  habit.required ? 'rgba(210,100,50,0.12)' : 'rgba(110,80,180,0.12)',
            color:       habit.required ? '#c8603a' : '#8a6abf',
          }}>
            {habit.required ? 'required' : 'recommended'}
          </span>
        )}
        {streak > 0 && (
          <span style={{
            fontSize:   13,
            fontWeight: 700,
            color:      '#b86020',
            fontFamily: tokens.font,
            lineHeight: 1,
          }}>
            🔥 {streak}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Completion persistence ───────────────────────────────────────────────────

function useScheduleDone() {
  const key = `trumpet_sched_done_${todayISO()}`;
  const [done, setDone] = React.useState<Set<string>>(() => {
    try { const r = localStorage.getItem(key); return r ? new Set(JSON.parse(r)) : new Set(); }
    catch { return new Set(); }
  });
  const toggle = React.useCallback((id: string) => {
    setDone(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      try { localStorage.setItem(key, JSON.stringify([...next])); } catch {}
      return next;
    });
  }, [key]);
  return { done, toggle };
}

// ─── Main tab ─────────────────────────────────────────────────────────────────

export function ScheduleTab() {
  const { status, items, gcalConnected }               = useSchedule();
  const { done, toggle }                              = useScheduleDone();
  const { pending, addedHabits, addHabit, dismissHabit, pendingCount } = useTeamHabitInbox();
  const [subTab, setSubTab] = React.useState<ScheduleSubTab>('all');

  React.useEffect(() => { initSimData(); }, []);

  const allEvents   = items.filter(i => !i.isHabit);
  const allHabits   = items.filter(i =>  i.isHabit);
  const totalHabits = allHabits.length + addedHabits.length;
  const stats       = useScheduleStats(items);

  return (
    <>
      {/* ── Left column ── */}
      <div style={{
        position: 'absolute',
        left:     70,
        top:      118,
        width:    680,
      }}>
        <ScheduleDateBlock />

        {/* Stats grid */}
        {status !== 'loading' && (
          <div style={{ marginTop: 36, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <StatPill
              emoji="📅"
              value={allEvents.length}
              label={`event${allEvents.length !== 1 ? 's' : ''} today`}
            />
            <StatPill
              emoji="⏱️"
              value={stats.hoursLabel || '—'}
              label="in meetings"
            />
            <StatPill
              emoji="🔄"
              value={totalHabits}
              label={`habit${totalHabits !== 1 ? 's' : ''} planned`}
            />
            <StatPill
              emoji="✅"
              value={`${[...allHabits, ...addedHabits.map(th => ({ id: th.id }))].filter(h => done.has(h.id)).length}/${totalHabits}`}
              label="habits done today"
            />
          </div>
        )}

        {/* Now / next block */}
        {stats.current && <CurrentBlock item={stats.current} />}
        {!stats.current && stats.upcoming && <NextUpBlock item={stats.upcoming} />}

        {status === 'loading' && (
          <div style={{ marginTop: 36, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {[1,2,3,4].map(i => <TrumpetSkeleton key={i} width="100%" height={72} radius={16} />)}
          </div>
        )}

        {/* Pending team habit recommendations */}
        {pending.length > 0 && status !== 'loading' && (
          <PendingRecommendations
            pending={pending}
            onAdd={addHabit}
            onDismiss={dismissHabit}
          />
        )}
      </div>

      {/* ── Right column ── */}
      <div style={{
        position: 'absolute',
        right:    60,
        top:      118,
        width:    620,
      }}>
        <div style={{
          borderRadius: 26,
          background:   tokens.cream,
          boxShadow:    '0 1px 0 rgba(255,255,255,0.4) inset, 0 30px 60px -30px rgba(0,0,0,0.6)',
          overflow:     'hidden',
        }}>
          {/* Card header */}
          <div style={{ padding: '26px 30px 0' }}>
            <div style={{ fontSize: 26, fontWeight: 700, color: tokens.ink, fontFamily: tokens.font, letterSpacing: -0.4, marginBottom: 16 }}>
              {todayISO() ? new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }) : 'Today'}
            </div>

            {/* Sub-tabs */}
            <div style={{ display: 'flex', gap: 22, position: 'relative' }}>
              <SubTabBtn label={`All (${items.length})`}           active={subTab === 'all'}    onClick={() => setSubTab('all')}    />
              <SubTabBtn label={`Events (${allEvents.length})`}    active={subTab === 'events'} onClick={() => setSubTab('events')} />
              <SubTabBtn label={`Habits (${totalHabits})`}         active={subTab === 'habits'} onClick={() => setSubTab('habits')} alertCount={pendingCount} />
            </div>
            <div style={{ height: 1.5, background: 'rgba(0,0,0,0.1)', marginTop: -1.5 }} />
          </div>

          {/* Scrollable list */}
          <div style={{
            padding:    '10px 30px 24px',
            maxHeight:  618,
            overflowY:  'auto',
            scrollbarWidth: 'none', // Firefox
          }}>
            {status === 'loading' && <ScheduleSkeletonFull />}

            {/* All — full merged list, same as home card */}
            {status !== 'loading' && subTab === 'all' && (
              <FullScheduleList
                items={items}
                done={done}
                onToggle={toggle}
              />
            )}

            {/* Events — calendar/time-blocked items only */}
            {status !== 'loading' && subTab === 'events' && allEvents.length === 0 && !gcalConnected && (
              <div style={{ padding: '32px 4px' }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: tokens.ink, fontFamily: tokens.font, marginBottom: 6 }}>
                  Connect Google Calendar
                </div>
                <div style={{ fontSize: 15, color: tokens.inkSoft, fontFamily: tokens.font, marginBottom: 18, lineHeight: 1.5 }}>
                  Your schedule will pull events from here automatically.
                </div>
                <div style={{
                  display:      'inline-flex',
                  alignItems:   'center',
                  gap:           8,
                  padding:      '10px 18px',
                  borderRadius:  12,
                  background:   'rgba(0,0,0,0.06)',
                  border:       '1px solid rgba(0,0,0,0.12)',
                  fontSize:      14,
                  fontWeight:    600,
                  color:         tokens.inkSoft,
                  fontFamily:    tokens.font,
                }}>
                  Go to You → Connected
                </div>
              </div>
            )}
            {status !== 'loading' && subTab === 'events' && (allEvents.length > 0 || gcalConnected) && (
              <FullScheduleList
                items={allEvents}
                done={done}
                onToggle={toggle}
              />
            )}

            {/* Habits — streak hero + personal + team */}
            {status !== 'loading' && subTab === 'habits' && (
              <HabitsList
                habits={allHabits}
                addedTeamHabits={addedHabits}
                doneTodayIds={done}
                onToggle={toggle}
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function SubTabBtn({ label, active, onClick, alertCount }: {
  label:       string;
  active:      boolean;
  onClick:     () => void;
  alertCount?: number;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize:      20,
        fontWeight:    600,
        color:         active ? tokens.ink : '#908aa0',
        paddingBottom: 12,
        cursor:        'pointer',
        position:      'relative',
        background:    'none',
        border:        'none',
        transition:    'color 0.15s',
        fontFamily:    tokens.font,
        display:       'flex',
        alignItems:    'center',
        gap:           6,
      }}
    >
      {label}
      {alertCount != null && alertCount > 0 && (
        <span style={{
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          minWidth:        18,
          height:          18,
          borderRadius:    99,
          background:     '#c8603a',
          color:          '#fff',
          fontSize:        11,
          fontWeight:      800,
          lineHeight:      1,
          padding:        '0 5px',
        }}>
          {alertCount}
        </span>
      )}
      {active && (
        <span style={{
          position:     'absolute',
          left: 0, right: 0, bottom: -1,
          height:        2.5,
          background:   tokens.ink,
          borderRadius: 2,
        }} />
      )}
    </button>
  );
}

function ScheduleSkeletonFull() {
  return (
    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 22 }}>
      {[100, 75, 90, 80].map((w, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '28px 1fr', gap: 14 }}>
          <TrumpetSkeleton width={26} height={26} radius={13} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            <TrumpetSkeleton width={80} height={15} />
            <TrumpetSkeleton width={`${w}%`} height={22} />
          </div>
        </div>
      ))}
    </div>
  );
}
