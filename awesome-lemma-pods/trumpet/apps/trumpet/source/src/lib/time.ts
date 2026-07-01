// Date / time helpers

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10); // "2026-06-09"
}

export function formatDateStamp(): { line1: string; line2: string } {
  const now = new Date();
  const month = now.toLocaleString('en-US', { month: 'short' }); // "Jun"
  const year  = String(now.getFullYear()).slice(2);               // "26"
  const day   = now.toLocaleString('en-US', { weekday: 'long' }); // "Monday"
  return { line1: `${month}'${year}`, line2: day };
}

export function currentDateNum(): string {
  return String(new Date().getDate()).padStart(2, '0'); // "09"
}

// Given ISO time strings (HH:MM or full ISO), is `now` within [start, end]?
export function isEventActive(startTime: string, endTime: string): boolean {
  const now   = new Date();
  const start = parseTime(startTime, now);
  const end   = parseTime(endTime, now);
  if (!start || !end) return false;
  return now >= start && now <= end;
}

function parseTime(t: string, ref: Date): Date | null {
  if (!t) return null;
  // Full ISO → parse directly
  if (t.includes('T')) return new Date(t);
  // "HH:MM" → build date from ref
  const [hStr, mStr] = t.split(':');
  const h = parseInt(hStr, 10);
  const m = parseInt(mStr, 10);
  if (isNaN(h) || isNaN(m)) return null;
  const d = new Date(ref);
  d.setHours(h, m, 0, 0);
  return d;
}

export function formatEventTime(iso: string): string {
  if (!iso) return '';
  // Already "HH:MM" style
  if (/^\d{2}:\d{2}$/.test(iso)) {
    const [h, m] = iso.split(':').map(Number);
    const suffix = h >= 12 ? 'PM' : 'AM';
    const hour   = h % 12 || 12;
    return m === 0 ? `${hour}:00 ${suffix}` : `${hour}:${String(m).padStart(2, '0')} ${suffix}`;
  }
  // Full ISO
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  } catch {
    return iso;
  }
}

// Compute due label relative to today
export function dueDateLabel(dueDateStr: string | null | undefined): { label: string; urgency: 'green' | 'amber' | 'red' } {
  if (!dueDateStr) return { label: 'No date', urgency: 'green' };
  const today  = new Date(); today.setHours(0,0,0,0);
  const due    = new Date(dueDateStr); due.setHours(0,0,0,0);
  const diffMs = due.getTime() - today.getTime();
  const diffD  = Math.round(diffMs / 86_400_000);

  if (diffD < 0)  return { label: 'Overdue', urgency: 'red' };
  if (diffD === 0) return { label: 'Today',   urgency: 'green' };
  if (diffD === 1) return { label: 'Tomorrow', urgency: 'amber' };
  if (diffD <= 3) return { label: `${diffD}d`,  urgency: 'amber' };
  const d = new Date(dueDateStr);
  return { label: d.toLocaleDateString('en-US', { weekday: 'short' }), urgency: 'green' };
}
