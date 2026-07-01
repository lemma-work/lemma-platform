/**
 * ScheduleCard — unified daily schedule.
 * Shows DB events, habits, and (when connected) Google Calendar events.
 * Habits get a small recurring pill. Active events are highlighted.
 */
import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { isEventActive, todayISO } from '@/lib/time';
import { TrumpetSkeleton } from '../shared/TrumpetSkeleton';
import type { ScheduleItem, ScheduleStatus } from '@/hooks/useSchedule';

interface Props {
  status:       ScheduleStatus;
  items:        ScheduleItem[];
  gcalConnected?: boolean;
  onViewAll?:   () => void;
}

// Persist completion in localStorage: key = "trumpet_sched_done_{date}" → Set<id>
function useScheduleDone() {
  const key = `trumpet_sched_done_${todayISO()}`;

  const [done, setDone] = React.useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch { return new Set(); }
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

export function ScheduleCard({ status, items, onViewAll }: Props) {
  const { done, toggle } = useScheduleDone();

  return (
    <div style={{
      borderRadius: 26,
      padding:      '30px 32px',
      background:   tokens.cream,
      boxShadow:    'var(--trumpet-card-shadow)',
      color:        tokens.ink,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 28, fontWeight: 700, letterSpacing: -0.4, color: tokens.ink, fontFamily: tokens.font }}>
          Today's schedule
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

      {/* Body */}
      {status === 'loading' && <ScheduleSkeleton />}

      {status === 'error' && (
        <p style={{ fontSize: 18, color: tokens.inkSoft, marginTop: 14, fontFamily: tokens.font }}>
          Couldn't load schedule. Try refreshing.
        </p>
      )}

      {status === 'empty' && (
        <div style={{ marginTop: 14 }}>
          <p style={{ fontSize: 18, color: tokens.inkSoft, marginTop: 0, fontFamily: tokens.font }}>
            Nothing on your schedule today.
          </p>
          <p style={{ fontSize: 16, color: '#a09890', marginTop: 6, fontFamily: tokens.font }}>
            Tell your assistant to block time — or connect Google Calendar to sync events.
          </p>
        </div>
      )}

      {(status === 'ready') && items.length > 0 && (
        // Scrollable window — ~3 rows visible (~168px), more on scroll
        <div style={{
          position:       'relative',
          marginTop:      14,
          maxHeight:      220,
          overflowY:      'auto',
          overflowX:      'hidden',
          scrollbarWidth: 'none',
          maskImage:      'linear-gradient(to bottom, black 88%, transparent 100%)',
          WebkitMaskImage:'linear-gradient(to bottom, black 88%, transparent 100%)',
        }}>
          {/* Vertical rail */}
          <div style={{
            position:   'absolute',
            left:        13,
            top:         26,
            bottom:      0,
            width:       2,
            background: 'rgba(0,0,0,0.13)',
            pointerEvents: 'none',
          }} />

          {items.map(item => {
            const isDone   = done.has(item.id);
            const isActive = !isDone && isEventActive(item.start_time, item.end_time);
            return (
              <ScheduleRow
                key={item.id}
                item={item}
                isDone={isDone}
                isActive={isActive}
                onToggle={() => toggle(item.id)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Row ──────────────────────────────────────────────────────────────────────

interface RowProps {
  item:     ScheduleItem;
  isDone:   boolean;
  isActive: boolean;
  onToggle: () => void;
}

function ScheduleRow({ item, isDone, isActive, onToggle }: RowProps) {
  const [hover, setHover] = React.useState(false);

  const rowStyle: React.CSSProperties = isActive ? {
    position:            'relative',
    zIndex:              1,
    display:             'grid',
    gridTemplateColumns: '28px 1fr',
    alignItems:          'start',
    gap:                 14,
    padding:             '16px 20px',
    background:          '#18120e',
    color:               tokens.fg,
    borderRadius:        16,
    margin:              '4px -8px',
    boxShadow:           '0 10px 26px -14px rgba(0,0,0,0.7)',
  } : {
    position:            'relative',
    zIndex:              1,
    display:             'grid',
    gridTemplateColumns: '28px 1fr',
    alignItems:          'start',
    gap:                 14,
    padding:             '12px 0',
  };

  return (
    <div style={rowStyle}>
      {/* Radio / check circle */}
      <div
        onClick={onToggle}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          width:          26,
          height:         26,
          borderRadius:   '50%',
          border:         isDone
            ? `2px solid ${tokens.green}`
            : isActive
              ? '2px solid rgba(243,239,230,0.7)'
              : '2px solid rgba(0,0,0,0.32)',
          background:     isDone ? tokens.green : isActive ? 'transparent' : tokens.cream,
          cursor:         'pointer',
          flexShrink:     0,
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          marginTop:      2,
          transform:      hover ? 'scale(1.08)' : 'scale(1)',
          transition:     'transform 0.12s, border-color 0.15s',
        }}
      >
        {isDone && <span style={{ color: '#fff', fontSize: 14, fontWeight: 800, lineHeight: 1 }}>✓</span>}
        {!isDone && isActive && (
          <span style={{ width: 11, height: 11, borderRadius: '50%', background: tokens.fg, display: 'block' }} />
        )}
      </div>

      {/* Right side */}
      <div>
        {/* Time row */}
        <div style={{
          display:    'flex',
          alignItems: 'center',
          gap:         8,
          marginBottom: 3,
        }}>
          <span style={{
            fontSize:   19,
            fontWeight: 600,
            color:      isActive ? '#b9b2a4' : '#6c665c',
            fontFamily: tokens.font,
          }}>
            {item.start_time}
            {item.end_time ? (
              <span style={{ fontWeight: 400, opacity: 0.65 }}> – {item.end_time}</span>
            ) : null}
          </span>

          {/* Habit pill */}
          {item.isHabit && (
            <span style={{
              fontSize:     12,
              fontWeight:   600,
              padding:      '2px 7px',
              borderRadius: 99,
              background:   isActive ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.09)',
              color:        isActive ? 'rgba(255,255,255,0.75)' : '#8a8178',
              letterSpacing: 0.3,
              fontFamily:   tokens.font,
            }}>
              habit
            </span>
          )}

          {/* Google Calendar source dot */}
          {item.source === 'gcal' && !item.isHabit && (
            <span style={{
              display:      'inline-block',
              width:         7,
              height:        7,
              borderRadius: '50%',
              background:   '#4285F4',
              flexShrink:   0,
              title:        'Google Calendar',
            }} />
          )}
        </div>

        {/* Title with emoji */}
        <div style={{
          fontSize:       23,
          fontWeight:     700,
          letterSpacing:  -0.2,
          color:          isActive ? '#fff' : tokens.ink,
          textDecoration: isDone ? 'line-through' : 'none',
          opacity:        isDone ? 0.5 : 1,
          fontFamily:     tokens.font,
          display:        'flex',
          alignItems:     'center',
          gap:             7,
        }}>
          <span style={{
            fontFamily: "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
            fontSize:   21,
            lineHeight: 1,
            flexShrink: 0,
          }}>
            {item.emoji}
          </span>
          {item.title}
        </div>

        {/* Description */}
        {item.description && (
          <div style={{
            fontSize:   18,
            fontWeight: 500,
            color:      isActive ? '#aaa294' : '#79736a',
            marginTop:  3,
            fontFamily: tokens.font,
          }}>
            {item.description}
          </div>
        )}
      </div>
    </div>
  );
}

function ScheduleSkeleton() {
  return (
    <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 20 }}>
      {[100, 80, 90].map((w, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '28px 1fr', gap: 14 }}>
          <TrumpetSkeleton width={26} height={26} radius={13} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <TrumpetSkeleton width={80} height={16} />
            <TrumpetSkeleton width={`${w}%`} height={20} />
          </div>
        </div>
      ))}
    </div>
  );
}
