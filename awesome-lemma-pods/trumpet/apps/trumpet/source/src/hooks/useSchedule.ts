/**
 * useSchedule — unified daily schedule.
 *
 * The schedule is DB-first, never empty when there's local data:
 *
 *   Layer 1 (immediate):  today's `type=calendar` records  +  all `type=habit` records
 *   Layer 2 (background): Google Calendar events via keeper — merged in when available
 *
 * If neither source has data, we show a "connect calendar" nudge.
 *
 * Data model:
 *   - `type=calendar`  → one-off events / blocked time (created by user or assistant)
 *   - `type=habit`     → recurring blocks with a preferred_time; shown at that time daily
 *   - Google Calendar  → external events pulled via keeper (read-only)
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { client } from '@/lib/client';
import { runtimeConfig } from '@/lib/runtime-config';
import { AGENTS, TABLES } from '@/lib/resources';
import { markSystemConversation } from '@/lib/system-conversations';
import { todayISO, formatEventTime } from '@/lib/time';
import { parseAssistantStreamEvent } from 'lemma-sdk';

// ─── Types ────────────────────────────────────────────────────────────────────

export type ScheduleStatus = 'loading' | 'ready' | 'empty' | 'error';

export interface ScheduleItem {
  id:          string;
  title:       string;
  start_time:  string;  // "9:00 AM" — always formatted
  end_time:    string;  // "10:00 AM" — may be '' if unknown
  description?: string;
  location?:   string;
  /** Source: 'db' = Lemma table, 'gcal' = Google Calendar */
  source:      'db' | 'gcal';
  /** Whether this is a recurring habit (shown every day) */
  isHabit:     boolean;
  /** Keyword-derived emoji — no AI, pure string matching */
  emoji:       string;
  /** 'personal' = private to the user · 'team' = admin-pushed compliance habit */
  scope?:      'personal' | 'team';
  /** true = compliance / required · false = recommended (team habits only) */
  required?:   boolean;
  /** Display label for who created the team habit, e.g. "Finance" */
  pushedBy?:   string;
}

// ─── Emoji picker — keyword matching, zero AI credits ────────────────────────

const EMOJI_RULES: Array<{ keywords: string[]; emoji: string }> = [
  // Movement / fitness
  { keywords: ['run', 'running', 'jog', 'jogging', 'sprint'],       emoji: '👟' },
  { keywords: ['gym', 'workout', 'exercise', 'lift', 'weights',
               'training', 'fitness', 'crossfit', 'hiit'],           emoji: '🏋️' },
  { keywords: ['yoga', 'stretch', 'pilates', 'meditat'],             emoji: '🧘' },
  { keywords: ['swim', 'swimming', 'pool'],                          emoji: '🏊' },
  { keywords: ['cycle', 'cycling', 'bike', 'biking', 'ride'],        emoji: '🚴' },
  { keywords: ['walk', 'walking', 'hike', 'hiking'],                 emoji: '🚶' },

  // Music / creative
  { keywords: ['music', 'guitar', 'piano', 'sing', 'singing',
               'practice', 'jam', 'drums', 'violin', 'instrument'],  emoji: '🎵' },
  { keywords: ['draw', 'drawing', 'sketch', 'art', 'paint'],         emoji: '🎨' },
  { keywords: ['write', 'writing', 'journal', 'journaling', 'blog'], emoji: '✍️' },

  // Work / meetings
  { keywords: ['standup', 'stand-up', 'stand up', 'daily'],         emoji: '☀️' },
  { keywords: ['retro', 'retrospective', 'review'],                  emoji: '🔁' },
  { keywords: ['demo', 'showcase', 'present', 'presentation'],       emoji: '🎯' },
  { keywords: ['interview', 'hiring', 'recruit'],                    emoji: '🤝' },
  { keywords: ['investor', 'fundraise', 'pitch', 'board', 'deck',
               'vc ', 'series'],                                      emoji: '📊' },
  { keywords: ['design', 'figma', 'ui', 'ux', 'wireframe',
               'mockup', 'prototype'],                                emoji: '🖼️' },
  { keywords: ['code', 'coding', 'engineering', 'dev ', 'pr ',
               'pull request', 'deploy', 'debug', 'infra', 'api'],   emoji: '💻' },
  { keywords: ['plan', 'planning', 'roadmap', 'strategy',
               'sprint', 'scope', 'kickoff'],                        emoji: '🗓️' },
  { keywords: ['call', 'zoom', 'meet', 'meeting', 'sync',
               'check-in', 'check in', 'catchup', 'catch up'],      emoji: '💬' },
  { keywords: ['1:1', '1-1', 'one-on-one', 'one on one'],           emoji: '👥' },
  { keywords: ['focus', 'deep work', 'block', 'no meetings',
               'heads down'],                                         emoji: '🎧' },

  // Learning
  { keywords: ['read', 'reading', 'book', 'article', 'paper',
               'research', 'learn', 'study', 'course'],              emoji: '📚' },

  // Food / social
  { keywords: ['lunch', 'dinner', 'breakfast', 'brunch', 'eat',
               'food', 'meal'],                                       emoji: '🍽️' },
  { keywords: ['coffee', 'cafe'],                                     emoji: '☕' },

  // Health
  { keywords: ['doctor', 'dentist', 'therapy', 'therapist',
               'appointment', 'health', 'medical'],                  emoji: '🏥' },
  { keywords: ['sleep', 'nap', 'rest'],                              emoji: '😴' },

  // Travel
  { keywords: ['flight', 'travel', 'airport', 'hotel', 'trip'],     emoji: '✈️' },
  { keywords: ['commute', 'drive', 'car'],                           emoji: '🚗' },
];

export function pickEmoji(title: string, isHabit: boolean): string {
  const t = title.toLowerCase();
  for (const rule of EMOJI_RULES) {
    if (rule.keywords.some(kw => t.includes(kw))) return rule.emoji;
  }
  // Fallback: habit vs one-off
  return isHabit ? '🔄' : '📌';
}

export interface UseScheduleResult {
  status:  ScheduleStatus;
  items:   ScheduleItem[];
  gcalConnected: boolean;
  refresh: () => void;
}

// ─── DB fetch helpers ─────────────────────────────────────────────────────────

async function fetchDbEvents(): Promise<ScheduleItem[]> {
  const today = todayISO();

  // Today's one-off calendar events (and manually blocked time)
  const eventsResult = await client.records.list(TABLES.commitments, {
    filters: [
      { field: 'type',     operator: 'eq', value: 'calendar' } as never,
      { field: 'due_date', operator: 'eq', value: today }      as never,
      { field: 'status',   operator: 'eq', value: 'active' }   as never,
    ],
    limit: 100,
  });

  // All active habits (no date — they show every day)
  const habitsResult = await client.records.list(TABLES.commitments, {
    filters: [
      { field: 'type',   operator: 'eq', value: 'habit'  } as never,
      { field: 'status', operator: 'eq', value: 'active' } as never,
    ],
    limit: 50,
  });

  const toItem = (r: Record<string, unknown>, isHabit: boolean): ScheduleItem | null => {
    const startRaw = (r.preferred_time ?? '') as string;
    const endRaw   = (r.end_time       ?? '') as string;
    if (!startRaw) return null; // no time → skip (can't place it on schedule)
    const title = r.title as string;
    return {
      id:          r.id as string,
      title,
      start_time:  formatEventTime(startRaw),
      end_time:    endRaw ? formatEventTime(endRaw) : '',
      description: r.description as string | undefined,
      source:      'db',
      isHabit,
      emoji:       pickEmoji(title, isHabit),
      scope:       'personal',
    };
  };

  const events = (eventsResult.items as Record<string, unknown>[])
    .map(r => toItem(r, false))
    .filter((x): x is ScheduleItem => x !== null);

  const habits = (habitsResult.items as Record<string, unknown>[])
    .map(r => toItem(r, true))
    .filter((x): x is ScheduleItem => x !== null);

  return sortByTime([...events, ...habits]);
}

// ─── Google Calendar fetch (keeper) ──────────────────────────────────────────

function buildGcalPrompt(): string {
  return (
    `Today is ${todayISO()}. ` +
    `Fetch today's Google Calendar events. ` +
    `If Google Calendar is not connected, reply with exactly: NOT_CONNECTED. ` +
    `Otherwise reply with ONLY a valid JSON array (no markdown, no explanation): ` +
    `[{"id":"...","title":"...","start_time":"HH:MM","end_time":"HH:MM","description":"..."}]. ` +
    `Sort by start_time ascending.`
  );
}

async function fetchGcalEvents(signal?: AbortSignal): Promise<{ items: ScheduleItem[]; connected: boolean }> {
  const conv   = await client.conversations.createForAgent(AGENTS.mrToot);
  markSystemConversation(conv.id);
  const stream = await client.conversations.sendMessageStream(
    conv.id,
    { content: buildGcalPrompt() },
    { signal },
  );

  const reader  = stream.getReader();
  const decoder = new TextDecoder();
  let   buffer  = '';
  let   text    = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (raw === '[DONE]' || !raw) continue;
        try {
          const evt = parseAssistantStreamEvent(JSON.parse(raw));
          if (evt?.type === 'text_delta' && evt.delta) text += evt.delta;
        } catch { /* skip malformed SSE */ }
      }
    }
  } finally {
    reader.releaseLock();
  }

  text = text.trim();
  if (text.includes('NOT_CONNECTED')) return { items: [], connected: false };

  const match = text.match(/\[[\s\S]*\]/);
  if (!match) return { items: [], connected: true };

  try {
    const raw = JSON.parse(match[0]) as Array<{
      id: string; title: string; start_time: string;
      end_time: string; description?: string;
    }>;
    const items: ScheduleItem[] = raw.map(e => ({
      id:          e.id,
      title:       e.title,
      start_time:  formatEventTime(e.start_time),
      end_time:    formatEventTime(e.end_time),
      description: e.description,
      source:      'gcal' as const,
      isHabit:     false,
      emoji:       pickEmoji(e.title, false),
    }));
    return { items, connected: true };
  } catch {
    return { items: [], connected: true };
  }
}

// ─── Sort helper ─────────────────────────────────────────────────────────────

/** Sort ScheduleItems by start_time ("9:00 AM" / "10:30 PM") */
function sortByTime(items: ScheduleItem[]): ScheduleItem[] {
  return [...items].sort((a, b) => {
    const ta = parseAmPm(a.start_time);
    const tb = parseAmPm(b.start_time);
    return ta - tb;
  });
}

function parseAmPm(t: string): number {
  // "9:00 AM" / "10:30 PM" / "" → minutes since midnight
  if (!t) return 9999;
  const m = t.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!m) return 9999;
  let h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const period = m[3].toUpperCase();
  if (period === 'PM' && h !== 12) h += 12;
  if (period === 'AM' && h === 12) h = 0;
  return h * 60 + min;
}

// ─── Cache ────────────────────────────────────────────────────────────────────

const GCAL_CACHE_KEY = 'trumpet_gcal_cache_v2';
const GCAL_CACHE_TTL = 5 * 60 * 1000;

function getGcalCache(): { items: ScheduleItem[]; connected: boolean } | null {
  try {
    const raw = sessionStorage.getItem(GCAL_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { ts: number; date: string; items: ScheduleItem[]; connected: boolean };
    if (Date.now() - parsed.ts > GCAL_CACHE_TTL) return null;
    if (parsed.date !== todayISO()) return null;
    return { items: parsed.items, connected: parsed.connected };
  } catch { return null; }
}

function setGcalCache(items: ScheduleItem[], connected: boolean) {
  try {
    sessionStorage.setItem(GCAL_CACHE_KEY, JSON.stringify({
      ts: Date.now(), date: todayISO(), items, connected,
    }));
  } catch {}
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useSchedule(): UseScheduleResult {
  const [status,        setStatus]        = useState<ScheduleStatus>('loading');
  const [items,         setItems]         = useState<ScheduleItem[]>([]);
  const [gcalConnected, setGcalConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async (force = false) => {
    // Abort any in-flight gcal request
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setStatus('loading');

    // ── Layer 1: DB (fast, always available) ──
    let dbItems: ScheduleItem[] = [];
    try {
      dbItems = await fetchDbEvents();
    } catch {
      // DB error: show error state and stop
      setStatus('error');
      return;
    }

    // Show DB data immediately — don't wait for gcal
    setItems(dbItems);
    setStatus(dbItems.length > 0 ? 'ready' : 'empty');

    if (!runtimeConfig.podId) return;

    // ── Layer 2: Google Calendar (background) ──
    if (!force) {
      const cached = getGcalCache();
      if (cached) {
        setGcalConnected(cached.connected);
        if (cached.items.length > 0) {
          // Merge: gcal events + db items, sorted
          setItems(mergeItems(dbItems, cached.items));
          setStatus('ready');
        }
        return;
      }
    }

    try {
      const { items: gcalItems, connected } = await fetchGcalEvents(ac.signal);
      if (ac.signal.aborted) return;

      setGcalCache(gcalItems, connected);
      setGcalConnected(connected);

      if (gcalItems.length > 0) {
        const merged = mergeItems(dbItems, gcalItems);
        setItems(merged);
        setStatus('ready');
      }
      // If gcal connected but returned nothing → keep DB items as-is
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      // gcal failure is non-fatal — DB items are already showing
    }
  }, []);

  useEffect(() => {
    load();
    return () => abortRef.current?.abort();
  }, [load]);

  return {
    status,
    items,
    gcalConnected,
    refresh: () => load(true),
  };
}

/**
 * Merge DB items and gcal items.
 * Simple strategy: keep all DB items; add gcal items that don't obviously
 * duplicate a DB item (same title, same start time).
 */
function mergeItems(db: ScheduleItem[], gcal: ScheduleItem[]): ScheduleItem[] {
  const dbKey = new Set(db.map(i => `${i.start_time}|${i.title.toLowerCase()}`));
  const unique = gcal.filter(g => !dbKey.has(`${g.start_time}|${g.title.toLowerCase()}`));
  return sortByTime([...db, ...unique]);
}
