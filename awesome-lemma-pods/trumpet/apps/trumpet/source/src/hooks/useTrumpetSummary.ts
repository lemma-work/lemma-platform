/**
 * Derives Trumpet's home-screen briefing text.
 * No keeper calls — pure computation from live data.
 *
 * Color contract:
 *   line.bright → tokens.fg  (near-white) — the KEY info: "🤝 4 commitments"
 *   line.muted  → tokens.muted            — context words: "to deliver"
 */
import { useMemo } from 'react';
import type { ScheduleItem } from './useSchedule';
import type { Commitment } from './useCommitments';

export interface TrumpetSummaryData {
  /** Short punchy opener — rotates by day, never same two days in a row */
  opener: string;
  lines:  SummaryLine[];
}

export interface SummaryLine {
  /** WHITE — emoji + count + noun e.g. "🤝 4 commitments" */
  bright: string;
  /** MUTED — context that follows e.g. "to deliver" */
  muted:  string;
}

// Rotates by day-of-week so it changes daily but never randomly flickers
const OPENERS = [
  "Here's the deal —",
  "Quick sitrep —",
  "Alright, listen —",
  "Eyes on the board —",
  "For the record —",
  "Real talk —",
  "Status check —",
];

export function useTrumpetSummary(
  _firstName:    string,
  outbound:      Commitment[],
  inbound:       Commitment[],
  scheduleItems: ScheduleItem[],
  _gcalConnected: boolean,
): TrumpetSummaryData {
  return useMemo(() => {
    const opener = OPENERS[new Date().getDay()];
    const lines: SummaryLine[] = [];

    if (outbound.length > 0) {
      const n = outbound.length;
      lines.push({
        bright: `🤝 ${n} commitment${n !== 1 ? 's' : ''}`,
        muted:  'to deliver',
      });
    }

    if (inbound.length > 0) {
      const n = inbound.length;
      lines.push({
        bright: `🤞 ${n} thing${n !== 1 ? 's' : ''}`,
        muted:  'owed to you',
      });
    }

    const events = scheduleItems.filter(i => !i.isHabit);
    if (events.length > 0) {
      const n = events.length;
      let totalMins = 0;
      for (const e of events) totalMins += estimateDuration(e.start_time, e.end_time);
      const timeStr = totalMins >= 60
        ? `${(totalMins / 60).toFixed(1).replace('.0', '')}h`
        : `${totalMins}m`;
      lines.push({
        bright: `📅 ${n} event${n !== 1 ? 's' : ''}`,
        muted:  `(${timeStr} total)`,
      });
    }

    return { opener, lines };
  }, [outbound, inbound, scheduleItems]);
}

function estimateDuration(start: string, end: string): number {
  if (!end) return 45;
  const a = parseAmPm(start), b = parseAmPm(end);
  return Math.max(0, b - a);
}

function parseAmPm(t: string): number {
  if (!t) return 0;
  const m = t.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!m) return 0;
  let h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const p = m[3].toUpperCase();
  if (p === 'PM' && h !== 12) h += 12;
  if (p === 'AM' && h === 12) h = 0;
  return h * 60 + min;
}
