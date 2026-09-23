/**
 * Which conversations this person keeps at the top, in this pod.
 *
 * Held on the device rather than on the conversation. A pin is a bookmark, not
 * a property of the thing bookmarked: the row already belongs to one user, so
 * nothing is shared or leaked by keeping the list local, and the whole feature
 * costs no migration and no write to the server. What it costs instead is that
 * pins do not follow you to another browser — the tradeoff, taken knowingly.
 *
 * The ids are the state. The conversations themselves are fetched by id when
 * they are not already on screen, so a pin from three weeks ago still draws
 * even though it fell out of the fifteen the sidebar lists.
 */

/** Enough to keep the good ones; few enough that the group cannot eat the column. */
export const MAX_PINNED_CONVERSATIONS = 8;

/**
 * Per user *and* per pod. The pod because pins belong to the work, the user
 * because one desktop install can be signed into more than one account and a
 * previous person's pins are not this person's.
 */
export function pinnedStorageKey(userId: string, podId: string): string {
    return `lemma.pinned-conversations.${userId}.${podId}`;
}

/**
 * Ids from stored text, believing none of it.
 *
 * Anything else in this key — hand-edited, half-written, left by an older
 * version — reads as "nothing pinned" rather than throwing inside a render.
 */
export function parsePinnedIds(raw: string | null): string[] {
    if (!raw) return [];

    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return [];
    }

    if (!Array.isArray(parsed)) return [];

    const ids: string[] = [];
    for (const value of parsed) {
        if (typeof value !== 'string') continue;
        const id = value.trim();
        if (!id || ids.includes(id)) continue;
        ids.push(id);
        if (ids.length === MAX_PINNED_CONVERSATIONS) break;
    }
    return ids;
}

/**
 * Pin, or unpin. Newest first, because the cap has to drop something and the
 * oldest pin is the one whose reason is most likely spent.
 */
export function togglePinnedId(ids: string[], id: string): string[] {
    if (ids.includes(id)) return withoutPinnedId(ids, id);
    return [id, ...ids].slice(0, MAX_PINNED_CONVERSATIONS);
}

export function withoutPinnedId(ids: string[], id: string): string[] {
    return ids.filter((pinned) => pinned !== id);
}
