import { key } from "@/session/storage";

/** When the tour offers itself, rather than waiting to be asked for.
 *
 *  An account's first week, once per person and per browser — the bargain
 *  the first-profile step makes, for the reason it gives: the account has
 *  nowhere to remember it yet, so somebody who skipped it may be offered it
 *  once more on a second device, inside the same week. After the week it is
 *  only ever asked for: the help menu, or `?tour=1` on any workspace link.
 *
 *  Not offered in the sample source, where there is no account and anybody
 *  judging a layout would meet it on every reload. */
export const TOUR_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

export function tourSeenKey(userId: string): string {
    return key("tour-seen:" + userId);
}

export function offersTour(createdAt: string | null | undefined, { seen, now = Date.now() }: { seen: boolean; now?: number }): boolean {
    if (seen || !createdAt) return false;
    const created = Date.parse(createdAt);
    if (Number.isNaN(created)) return false;
    return now - created <= TOUR_WINDOW_MS;
}

export function readTourSeen(userId: string): boolean {
    try {
        return localStorage.getItem(tourSeenKey(userId)) === "1";
    } catch {
        /* Storage refused: offered again next time, which is the whole cost. */
        return false;
    }
}

export function writeTourSeen(userId: string): void {
    try {
        localStorage.setItem(tourSeenKey(userId), "1");
    } catch {
        /* As above. */
    }
}
