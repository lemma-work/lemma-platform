/**
 * Calendar hook — fetches today's events via the keeper agent.
 *
 * If Google Calendar isn't connected, keeper will say so and we fall back to
 * reading `type=calendar` records from the Lemma commitments table — using
 * `due_date` + `preferred_time` / `end_time` as the event time range.
 *
 * The keeper response is cached in sessionStorage for 5 minutes so repeated
 * renders don't re-fire the conversation.
 */
import { useState, useEffect, useCallback } from 'react';
import { client } from '@/lib/client';
import { markSystemConversation } from '@/lib/system-conversations';
import { runtimeConfig } from '@/lib/runtime-config';
import { AGENTS, TABLES } from '@/lib/resources';
import { todayISO, formatEventTime } from '@/lib/time';
import { parseAssistantStreamEvent } from 'lemma-sdk';

export type CalendarStatus = 'idle' | 'loading' | 'ready' | 'not_connected' | 'error';

export interface CalendarEvent {
  id:          string;
  title:       string;
  start_time:  string; // "HH:MM" or ISO
  end_time:    string;
  description?: string;
  location?:   string;
}

export interface UseCalendarResult {
  status:  CalendarStatus;
  events:  CalendarEvent[];
  refresh: () => void;
}

const CACHE_KEY  = 'trumpet_cal_cache';
const CACHE_TTL  = 5 * 60 * 1000; // 5 min

interface CacheEntry { ts: number; events: CalendarEvent[]; date: string }

function getCache(): CalendarEvent[] | null {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const { ts, events, date } = JSON.parse(raw) as CacheEntry;
    if (Date.now() - ts > CACHE_TTL) return null;
    if (date !== todayISO()) return null; // stale if day rolled over
    return events;
  } catch { return null; }
}

function setCache(events: CalendarEvent[]) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), events, date: todayISO() }));
  } catch {}
}

/** Stream a keeper conversation to completion, returning the full text. */
async function streamToText(
  conversationId: string,
  signal?: AbortSignal
): Promise<string> {
  const stream = await client.conversations.sendMessageStream(
    conversationId,
    { content: buildCalendarPrompt() },
    { signal }
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
        } catch { /* skip malformed SSE frame */ }
      }
    }
  } finally {
    reader.releaseLock();
  }
  return text.trim();
}

function buildCalendarPrompt(): string {
  return (
    `Today is ${todayISO()}. ` +
    `Fetch today's Google Calendar events. ` +
    `If Google Calendar is not connected, reply with exactly: NOT_CONNECTED. ` +
    `Otherwise, reply with ONLY a valid JSON array (no markdown, no explanation) using this shape: ` +
    `[{"id":"...","title":"...","start_time":"HH:MM","end_time":"HH:MM","description":"...","location":"..."}]. ` +
    `Sort by start_time ascending. Include all events for today.`
  );
}

function parseEvents(text: string): { events: CalendarEvent[]; connected: boolean } {
  if (text.includes('NOT_CONNECTED')) return { events: [], connected: false };
  // Extract JSON array from anywhere in the text (keeper may add a preamble)
  const match = text.match(/\[[\s\S]*\]/);
  if (!match) return { events: [], connected: true };
  try {
    const raw: CalendarEvent[] = JSON.parse(match[0]);
    const events = raw.map(e => ({
      ...e,
      start_time: formatEventTime(e.start_time),
      end_time:   formatEventTime(e.end_time),
    }));
    return { events, connected: true };
  } catch {
    return { events: [], connected: true };
  }
}

/** Fallback: read type=calendar records from Lemma DB for today. */
async function fetchDbCalendarEvents(): Promise<CalendarEvent[]> {
  try {
    const today = todayISO();
    const result = await client.records.list(TABLES.commitments, {
      filters: [
        { field: 'type',     operator: 'eq', value: 'calendar' },
        { field: 'due_date', operator: 'eq', value: today },
        { field: 'status',   operator: 'eq', value: 'active' },
      ],
      sort:  [{ field: 'preferred_time', direction: 'asc' }],
      limit: 50,
    });

    return (result.items as Record<string, unknown>[]).map(r => ({
      id:          r.id as string,
      title:       r.title as string,
      start_time:  r.preferred_time ? formatEventTime(r.preferred_time as string) : '—',
      end_time:    r.end_time       ? formatEventTime(r.end_time as string)        : '',
      description: r.description as string | undefined,
      location:    r.notes as string | undefined,
    }));
  } catch {
    return [];
  }
}

export function useCalendar(): UseCalendarResult {
  const [status, setStatus] = useState<CalendarStatus>('idle');
  const [events, setEvents] = useState<CalendarEvent[]>([]);

  const fetch = useCallback(async (force = false) => {
    if (!force) {
      const cached = getCache();
      if (cached) { setEvents(cached); setStatus('ready'); return; }
    }

    setStatus('loading');
    const ac = new AbortController();

    try {
      if (!runtimeConfig.podId) {
        // No pod — fall back to DB
        const dbEvents = await fetchDbCalendarEvents();
        setEvents(dbEvents);
        setStatus('ready');
        return;
      }

      const conv = await client.conversations.createForAgent(AGENTS.mrToot);
      markSystemConversation(conv.id);
      const text = await streamToText(conv.id, ac.signal);
      const { events: parsed, connected } = parseEvents(text);

      if (!connected) {
        // Keeper says not connected — fall back to DB calendar records
        const dbEvents = await fetchDbCalendarEvents();
        setEvents(dbEvents);
        // Show as 'ready' with DB data so ScheduleCard renders normally
        setStatus(dbEvents.length > 0 ? 'ready' : 'not_connected');
        return;
      }
      setCache(parsed);
      setEvents(parsed);
      setStatus('ready');
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      // On any error, try DB fallback before giving up
      try {
        const dbEvents = await fetchDbCalendarEvents();
        if (dbEvents.length > 0) {
          setEvents(dbEvents);
          setStatus('ready');
          return;
        }
      } catch { /* ignore fallback failure */ }
      setStatus('error');
    }

    return () => ac.abort();
  }, []);

  useEffect(() => { fetch(); }, [fetch]);

  return { status, events, refresh: () => fetch(true) };
}
