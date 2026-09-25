import { key } from "./storage";

/** Whether this tab was just sent to sign in.
 *
 *  Every automatic hop between the workspace and the portal reads this, and it
 *  is what stops them bouncing a person between each other. The portal sends
 *  anybody `GET /users/me` accepts straight back; the workspace sends anybody
 *  its session layer has marked signed out straight to the portal. When those
 *  two disagree — `/users/me` fine, some other request answering 401 — each is
 *  right by its own lights and the tab ping-pongs forever.
 *
 *  It used to be a flag cleared the moment the workspace said "in", which is
 *  exactly the moment that restarts the loop. So it is a time instead, and it
 *  expires rather than being cleared: within `RECENT_MS` of a trip the
 *  workspace shows "we couldn't finish signing you in" instead of redirecting,
 *  and the landing page stops sending the tab back into the workspace. After
 *  it, a session that ends mid-afternoon goes to sign-in on its own again.
 *
 *  `sessionStorage`, because the question is about this tab and must not be
 *  inherited by the next one. */
const SENT = key("sent-to-portal");

export const RECENT_MS = 2 * 60_000;

export function tripIsRecent(stored: string | null, now: number): boolean {
    if (!stored) return false;
    const at = Number(stored);
    /* Earlier builds stored "1", with no time. Reading that as an ancient trip
       is the answer that cannot strand anybody. */
    return Number.isFinite(at) && at <= now && now - at < RECENT_MS;
}

export const portalTrip = {
    mark(now = Date.now()) {
        try { sessionStorage.setItem(SENT, String(now)); } catch { /* no storage */ }
    },
    recent(now = Date.now()): boolean {
        try { return tripIsRecent(sessionStorage.getItem(SENT), now); } catch { return false; }
    },
    clear() {
        try { sessionStorage.removeItem(SENT); } catch { /* no storage */ }
    },
};
