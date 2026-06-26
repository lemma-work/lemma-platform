/**
 * sim-data — deterministic seed data for the Habits demo.
 *
 * Team habits and simulated teammates are seeded here. On first mount,
 * initSimData() writes team habit completions into the same localStorage
 * keys that useScheduleDone uses, so the streak hook works uniformly for
 * both personal and team habits.
 */

export interface SimTeamHabit {
  id:       string;
  title:    string;
  emoji:    string;
  time:     string;    // display label, e.g. "9:00 AM" or "Every Friday"
  cadence:  'daily' | 'weekly';
  required: boolean;
  pushedBy: string;
}

export interface SimTeamMember {
  id:          string;
  name:        string;
  initials:    string;
  color:       string;
  streakDays:  number;
}

export const TEAM_HABITS: SimTeamHabit[] = [
  {
    id:       'sim-th-1',
    title:    'Submit expense report',
    emoji:    '🧾',
    time:     'Every Friday',
    cadence:  'weekly',
    required: true,
    pushedBy: 'Finance',
  },
  {
    id:       'sim-th-2',
    title:    'Post async standup',
    emoji:    '📋',
    time:     '9:00 AM',
    cadence:  'daily',
    required: false,
    pushedBy: 'Your manager',
  },
  {
    id:       'sim-th-3',
    title:    'Update project tracker',
    emoji:    '🗂️',
    time:     'Every Monday',
    cadence:  'weekly',
    required: true,
    pushedBy: 'Operations',
  },
];

export const TEAM_MEMBERS: SimTeamMember[] = [
  { id: 'sim-m-1', name: 'Sarah R.',   initials: 'SR', color: '#4a3a7a', streakDays: 18 },
  { id: 'sim-m-2', name: 'Mike T.',    initials: 'MT', color: '#5a3a3a', streakDays: 9  },
  { id: 'sim-m-3', name: 'Jamie P.',   initials: 'JP', color: '#3a4a5a', streakDays: 6  },
  { id: 'sim-m-4', name: 'Alex L.',    initials: 'AL', color: '#4a4a3a', streakDays: 4  },
  { id: 'sim-m-5', name: 'Taylor K.',  initials: 'TK', color: '#3a5a4a', streakDays: 2  },
];

const SEED_KEY = 'trumpet_sim_seeded_v1';

function isoOffset(daysBack: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return d.toISOString().slice(0, 10);
}

function addCompletion(habitId: string, daysBack: number): void {
  const key = `trumpet_sched_done_${isoOffset(daysBack)}`;
  try {
    const existing: string[] = JSON.parse(localStorage.getItem(key) ?? '[]');
    if (!existing.includes(habitId)) {
      existing.push(habitId);
      localStorage.setItem(key, JSON.stringify(existing));
    }
  } catch {}
}

/**
 * Seeds completion history for the three sim team habits so their streaks
 * look realistic on first load. Safe to call on every mount — no-ops after
 * the first run.
 */
export function initSimData(): void {
  if (localStorage.getItem(SEED_KEY)) return;

  // Async standup — daily, seed last 5 days → streak of 5
  for (let i = 0; i < 5; i++) addCompletion('sim-th-2', i);

  // Expense report — weekly (Fridays), seed 2 occurrences
  addCompletion('sim-th-1', 0);
  addCompletion('sim-th-1', 7);

  // Project tracker — weekly (Mondays), seed 2 occurrences
  addCompletion('sim-th-3', 0);
  addCompletion('sim-th-3', 7);

  localStorage.setItem(SEED_KEY, '1');
}

/** Returns leaderboard sorted by streak, with the real user's streak injected. */
export function getTeamLeaderboard(myStreak: number): Array<{
  id: string; name: string; initials: string; color: string; streak: number; isMe: boolean;
}> {
  const rows = TEAM_MEMBERS.map(m => ({ ...m, streak: m.streakDays, isMe: false }));
  rows.push({ id: 'me', name: 'You', initials: 'Me', color: '#3a5a3a', streak: myStreak, isMe: true });
  return rows.sort((a, b) => b.streak - a.streak);
}
