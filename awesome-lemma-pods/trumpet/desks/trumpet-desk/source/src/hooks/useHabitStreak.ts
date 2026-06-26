import * as React from 'react';

/**
 * Reads consecutive-day completions for habitId from the same localStorage
 * keys that useScheduleDone writes to (trumpet_sched_done_YYYY-MM-DD).
 * Returns the current streak — days in an unbroken run ending today.
 */
export function computeStreak(habitId: string): number {
  let streak = 0;
  for (let i = 0; i < 90; i++) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const key = `trumpet_sched_done_${d.toISOString().slice(0, 10)}`;
    try {
      const ids: string[] = JSON.parse(localStorage.getItem(key) ?? '[]');
      if (ids.includes(habitId)) {
        streak++;
      } else {
        break;
      }
    } catch {
      break;
    }
  }
  return streak;
}

/**
 * Hook wrapper — re-runs whenever `doneToday` flips so the streak
 * updates immediately after a check-off.
 */
export function useHabitStreak(habitId: string, doneToday: boolean): number {
  return React.useMemo(
    () => computeStreak(habitId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [habitId, doneToday],
  );
}
