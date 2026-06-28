// Tracks conversation IDs created by background hooks (calendar, schedule).
// Persisted to localStorage so they survive page reloads.
const STORAGE_KEY = 'trumpet_system_conv_ids';

function loadIds(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

const ids = loadIds();

export function markSystemConversation(id: string) {
  ids.add(id);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
  } catch {}
}

export function isSystemConversation(id: string): boolean {
  return ids.has(id);
}
