/**
 * "3d ago" — one clock, shared.
 *
 * This lived inside `components/pod/recent-conversations`, which is fine while
 * only pod surfaces read it and wrong the moment anything else does: the home
 * pod list wants one date string and would have pulled the conversation list,
 * its start button and the assistant navigation helpers along with it.
 *
 * Returns null rather than a placeholder for a missing or unparseable value, so
 * a caller can decide whether the absence deserves a line at all.
 */
export function formatRelativeTime(value?: string | null): string | null {
    if (!value) return null;
    const then = new Date(value).getTime();
    if (Number.isNaN(then)) return null;
    const diffSec = Math.round((Date.now() - then) / 1000);
    if (diffSec < 45) return 'just now';
    const diffMin = Math.round(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.round(diffHr / 24);
    if (diffDay < 7) return `${diffDay}d ago`;
    const diffWk = Math.round(diffDay / 7);
    if (diffWk < 5) return `${diffWk}w ago`;
    const diffMo = Math.round(diffDay / 30);
    if (diffMo < 12) return `${diffMo}mo ago`;
    return `${Math.round(diffDay / 365)}y ago`;
}
