/**
 * useNotes — fetch and organise note_index records.
 *
 * Extra fields (pinned, color) are stored as JSON in the `notes` column
 * because the table schema is fixed. We parse them out here.
 */
import * as React from 'react';
import { useRecords } from 'lemma-sdk/react';
import { client } from '@/lib/client';
import { runtimeConfig } from '@/lib/runtime-config';
import { TABLES } from '@/lib/resources';

// ─── Types ────────────────────────────────────────────────────────────────────

export type NoteCategory =
  | 'idea' | 'content' | 'meeting' | 'reference' | 'task' | 'personal' | 'other';

export interface NoteRecord {
  id:         string;
  title:      string;
  summary:    string;
  keywords:   string;
  category:   NoteCategory;
  file_path:  string;
  pinned:     boolean;
  color:      string;
  body:       string;   // full markdown body (dedicated column)
  notes:      string;   // raw JSON column — contains {pinned, color, stage, source}
  created_at: string;
  updated_at: string;
}

export interface FolderGroup {
  category:    NoteCategory;
  count:       number;
  notes:       NoteRecord[];
  recentNotes: NoteRecord[]; // top 3 by recency
}

export interface UseNotesResult {
  notes:             NoteRecord[];
  recentNotes:       NoteRecord[];
  folders:           FolderGroup[];
  isLoading:         boolean;
  error:             Error | null;
  activeCategory:    NoteCategory | null;
  setActiveCategory: (cat: NoteCategory | null) => void;
  refresh:           () => void;
}

// ─── Category display config ──────────────────────────────────────────────────

export const CATEGORY_CONFIG: Record<NoteCategory, {
  label:       string;
  emoji:       string;
  stickyColor: string;
  folderColor: string;
  textColor:   string;
}> = {
  content:   { label: 'Content Ideas',  emoji: '✍️',  stickyColor: '#f5e6a3', folderColor: '#f6d775', textColor: '#2a2318' },
  personal:  { label: 'Product Dev',    emoji: '🛠️',  stickyColor: '#e6e0f1', folderColor: '#d9cef0', textColor: '#211e2e' },
  task:      { label: 'Launch',         emoji: '🚀',  stickyColor: '#c8e6d4', folderColor: '#cfe7cf', textColor: '#1a2e1e' },
  idea:      { label: 'Ideas',          emoji: '💡',  stickyColor: '#ffd5b8', folderColor: '#f6c89a', textColor: '#2e1e10' },
  meeting:   { label: 'Meetings',       emoji: '💬',  stickyColor: '#c5d8f5', folderColor: '#b8d0f0', textColor: '#12203a' },
  reference: { label: 'Reference',      emoji: '📚',  stickyColor: '#f0e0c8', folderColor: '#e8d0a8', textColor: '#2a1e0e' },
  other:     { label: 'Other',          emoji: '📌',  stickyColor: '#e8e4de', folderColor: '#ddd8d0', textColor: '#2a2820' },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function parseMeta(raw: string | null | undefined): { pinned: boolean; color: string } {
  try {
    const obj = JSON.parse(raw ?? '{}');
    return { pinned: !!obj.pinned, color: obj.color ?? '' };
  } catch {
    return { pinned: false, color: '' };
  }
}

function toNote(r: Record<string, unknown>): NoteRecord {
  const cat  = (r.category as NoteCategory) ?? 'other';
  const cfg  = CATEGORY_CONFIG[cat];
  const meta = parseMeta(r.notes as string);
  return {
    id:         r.id as string,
    title:      r.title as string,
    summary:    r.summary as string,
    keywords:   (r.keywords as string) ?? '',
    category:   cat,
    file_path:  r.file_path as string,
    pinned:     meta.pinned,
    color:      meta.color || cfg.stickyColor,
    body:       (r.body as string) ?? '',
    notes:      (r.notes as string) ?? '',
    created_at: r.created_at as string,
    updated_at: r.updated_at as string,
  };
}

const CATEGORY_ORDER: NoteCategory[] = ['content', 'personal', 'task', 'idea', 'meeting', 'reference', 'other'];

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useNotes(): UseNotesResult {
  const [activeCategory, setActiveCategory] = React.useState<NoteCategory | null>(null);

  const state = useRecords<Record<string, unknown>>({
    client,
    podId:     runtimeConfig.podId,
    tableName: TABLES.noteIndex,
    sort:      [{ field: 'updated_at', direction: 'desc' }],
    limit:     200,
  });

  const notes = React.useMemo(
    () => state.records.map(toNote),
    [state.records],
  );

  const recentNotes = React.useMemo(
    () => [...notes].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [notes],
  );

  const folders = React.useMemo<FolderGroup[]>(() => {
    const map = new Map<NoteCategory, NoteRecord[]>();
    for (const note of notes) {
      const arr = map.get(note.category) ?? [];
      arr.push(note);
      map.set(note.category, arr);
    }
    const result: FolderGroup[] = [];
    for (const [cat, catNotes] of map.entries()) {
      const sorted = [...catNotes].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      result.push({ category: cat, count: sorted.length, notes: sorted, recentNotes: sorted.slice(0, 3) });
    }
    return result.sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category));
  }, [notes]);

  return {
    notes,
    recentNotes,
    folders,
    isLoading:         state.isLoading,
    error:             state.error ?? null,
    activeCategory,
    setActiveCategory,
    refresh:           state.refresh,
  };
}

// ─── Relative time helper ─────────────────────────────────────────────────────

export function relativeTime(isoString: string): string {
  const now  = Date.now();
  const then = new Date(isoString).getTime();
  const diff = now - then;

  const mins  = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days  = Math.floor(diff / 86_400_000);

  if (mins < 2)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days === 1) return 'yesterday';
  if (days < 7)   return `${days}d ago`;
  return new Date(isoString).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
