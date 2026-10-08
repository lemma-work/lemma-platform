import { siteOrigin } from "./config";

/** Sending a signed-in person back to an app that does not see their session.
 *
 *  The portal answers "already signed in" by returning the person to the app
 *  that sent them. If the app disagrees -- its own copy of the session is
 *  stale, or it cannot read the shared cookies at all -- it sends them straight
 *  back, and the two bounce faster than anyone can read either page, with
 *  nothing anywhere recording an error. The portal is the one place that sees
 *  the repetition, so it is the one that stops it.
 *
 *  Only another origin counts. This site's own pages share the portal's session
 *  markers, so they cannot disagree with it about whether there is a session.
 */

const KEY = "lemma-app:auth:returns";

/** How far back a return still counts. A loop repeats within a second or two;
 *  somebody signing in to the same app twice on purpose is minutes apart. */
export const WINDOW_MS = 60_000;

/** Returns already made in the window that make the next one a loop. The
 *  person is stopped on the third: one return is the ordinary case, and a
 *  second is an app that reloaded once on its own. */
export const RETURNS_BEFORE_LOOP = 2;

export type Returns = Record<string, number[]>;

/** The app origin a destination belongs to, or null for this site's own pages. */
export function appOrigin(destination: string, here: string): string | null {
    try {
        const origin = new URL(destination, here).origin;
        return origin === new URL(here).origin ? null : origin;
    } catch {
        return null;
    }
}

export function isLoop(returns: Returns, origin: string, now: number): boolean {
    return (returns[origin] ?? []).filter((at) => now - at < WINDOW_MS).length >= RETURNS_BEFORE_LOOP;
}

/** The record with this return added and everything outside the window dropped. */
export function withReturn(returns: Returns, origin: string, now: number): Returns {
    const next: Returns = {};
    for (const [key, times] of Object.entries(returns)) {
        const kept = times.filter((at) => now - at < WINDOW_MS);
        if (kept.length > 0) next[key] = kept;
    }
    next[origin] = [...(next[origin] ?? []), now];
    return next;
}

export function withoutOrigin(returns: Returns, origin: string): Returns {
    const next = { ...returns };
    delete next[origin];
    return next;
}

/** Whatever was stored, as a record or as nothing. Session storage belongs to
 *  this tab, which is where a loop happens, and a value somebody edited by hand
 *  must not be able to stop a sign-in. */
export function parseReturns(raw: string | null): Returns {
    if (!raw) return {};
    try {
        const parsed: unknown = JSON.parse(raw);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
        const returns: Returns = {};
        for (const [origin, times] of Object.entries(parsed)) {
            if (!Array.isArray(times)) continue;
            const numbers = times.filter((at): at is number => typeof at === "number" && Number.isFinite(at));
            if (numbers.length > 0) returns[origin] = numbers;
        }
        return returns;
    } catch {
        return {};
    }
}

function stored(): Returns {
    try {
        return parseReturns(window.sessionStorage.getItem(KEY));
    } catch {
        return {};
    }
}

function store(returns: Returns): void {
    try {
        window.sessionStorage.setItem(KEY, JSON.stringify(returns));
    } catch {
        /* A browser refusing session storage gets no guard, which is how the
           portal behaved before it had one. */
    }
}

/** Whether returning the person to `destination` now would be the third time in a minute. */
export function wouldLoop(destination: string): boolean {
    const origin = appOrigin(destination, siteOrigin());
    return origin !== null && isLoop(stored(), origin, Date.now());
}

/** Count one automatic return. Only the portal's own "already signed in" hand-back
 *  is counted: a person who signs in by hand is not looping. */
export function noteReturn(destination: string): void {
    const origin = appOrigin(destination, siteOrigin());
    if (origin !== null) store(withReturn(stored(), origin, Date.now()));
}

/** Start the count again, for a person who asked to try once more. */
export function forgetReturns(destination: string): void {
    const origin = appOrigin(destination, siteOrigin());
    if (origin !== null) store(withoutOrigin(stored(), origin));
}
