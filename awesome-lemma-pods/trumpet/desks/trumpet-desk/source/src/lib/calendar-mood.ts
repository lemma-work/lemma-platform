import type { ScheduleItem } from '@/hooks/useSchedule';

export type CalendarMood = 'chill' | 'average' | 'overwhelmed';

export function getCalendarMood(items: ScheduleItem[]): CalendarMood {
  const events = items.filter(item => !item.isHabit);
  const totalMinutes = events.reduce(
    (sum, item) => sum + estimateDuration(item.start_time, item.end_time),
    0,
  );

  if (events.length <= 1 && totalMinutes <= 90) return 'chill';
  if (events.length >= 5 || totalMinutes >= 270 || hasDenseCalendar(events)) return 'overwhelmed';
  return 'average';
}

function estimateDuration(start: string, end: string): number {
  if (!end) return 45;
  const startMinutes = parseAmPm(start);
  const endMinutes = parseAmPm(end);
  if (startMinutes === null || endMinutes === null) return 45;
  return Math.max(0, endMinutes - startMinutes);
}

function parseAmPm(value: string): number | null {
  const match = value.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!match) return null;

  let hours = Number.parseInt(match[1], 10);
  const minutes = Number.parseInt(match[2], 10);
  const period = match[3].toUpperCase();

  if (period === 'PM' && hours !== 12) hours += 12;
  if (period === 'AM' && hours === 12) hours = 0;

  return hours * 60 + minutes;
}

function hasDenseCalendar(events: ScheduleItem[]): boolean {
  if (events.length < 3) return false;

  const timed = events
    .map(event => ({
      start: parseAmPm(event.start_time),
      end: event.end_time ? parseAmPm(event.end_time) : null,
    }))
    .filter((event): event is { start: number; end: number | null } => event.start !== null)
    .sort((a, b) => a.start - b.start);

  let tightTransitions = 0;
  for (let index = 1; index < timed.length; index += 1) {
    const previousEnd = timed[index - 1].end ?? timed[index - 1].start + 45;
    const gap = timed[index].start - previousEnd;
    if (gap >= 0 && gap <= 15) tightTransitions += 1;
  }

  return tightTransitions >= 2;
}
