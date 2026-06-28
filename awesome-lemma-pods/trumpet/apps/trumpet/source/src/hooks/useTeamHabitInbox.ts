/**
 * useTeamHabitInbox — tracks which sim team habits the user has added vs dismissed.
 *
 * State lives in localStorage so it persists across refreshes.
 * "sim-th-1" (Submit expense report) is pre-seeded as already added so the
 * Team section is never empty on first load.
 */
import * as React from 'react';
import { TEAM_HABITS } from '@/lib/sim-data';
import type { SimTeamHabit } from '@/lib/sim-data';

const ADDED_KEY     = 'trumpet_team_habits_added_v1';
const DISMISSED_KEY = 'trumpet_team_habits_dismissed_v1';

function readIds(key: string, fallback: string[]): string[] {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeIds(key: string, ids: string[]): void {
  try { localStorage.setItem(key, JSON.stringify(ids)); } catch {}
}

export interface TeamHabitInboxResult {
  pending:      SimTeamHabit[];
  addedHabits:  SimTeamHabit[];
  pendingCount: number;
  addHabit:     (id: string) => void;
  dismissHabit: (id: string) => void;
}

export function useTeamHabitInbox(): TeamHabitInboxResult {
  // Pre-seed: expense report is added by default so Team section isn't empty
  const [added,     setAdded]     = React.useState<string[]>(() => readIds(ADDED_KEY,     ['sim-th-1']));
  const [dismissed, setDismissed] = React.useState<string[]>(() => readIds(DISMISSED_KEY, []));

  const pending     = TEAM_HABITS.filter(h => !added.includes(h.id) && !dismissed.includes(h.id));
  const addedHabits = TEAM_HABITS.filter(h => added.includes(h.id));

  const addHabit = React.useCallback((id: string) => {
    setAdded(prev => {
      const next = prev.includes(id) ? prev : [...prev, id];
      writeIds(ADDED_KEY, next);
      return next;
    });
  }, []);

  const dismissHabit = React.useCallback((id: string) => {
    setDismissed(prev => {
      const next = prev.includes(id) ? prev : [...prev, id];
      writeIds(DISMISSED_KEY, next);
      return next;
    });
  }, []);

  return { pending, addedHabits, pendingCount: pending.length, addHabit, dismissHabit };
}
