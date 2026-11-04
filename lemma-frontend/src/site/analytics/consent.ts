/**
 * Analytics consent, stored on the device.
 *
 * Lemma Cloud reports to PostHog in the EU, which is the simplest data-protection
 * posture available and the reason the region was chosen. Setting identifying
 * cookies on first paint would undercut that, so persistence starts in memory —
 * the session is measurable, nothing is written to the device — and is upgraded
 * to `localStorage+cookie` only after someone accepts.
 *
 * A rejection is remembered too, and remembered as an explicit `false` rather
 * than an absence, so the banner does not come back every visit to ask a
 * question that has been answered.
 */

const STORAGE_KEY = "lemma:analytics-consent";

export type ConsentDecision = "granted" | "denied" | "unanswered";

let sessionDecision: ConsentDecision | null = null;

function readStorage(): string | null {
    try {
        return window.localStorage.getItem(STORAGE_KEY);
    } catch {
        // Safari in private mode, or storage disabled entirely. An unreadable
        // preference is an unanswered one, which keeps us cookieless.
        return null;
    }
}

export function readConsentDecision(): ConsentDecision {
    if (typeof window === "undefined") return "unanswered";
    const raw = sessionDecision ?? readStorage();
    if (raw === "granted" || raw === "denied") return raw;
    return "unanswered";
}

export function hasAnalyticsConsent(): boolean {
    return readConsentDecision() === "granted";
}

/**
 * Where the question is asked: the public site, whose visitors are the people
 * the answer is for.
 *
 * Sign-in, the hand-offs on either side of it, and the workspace are left out.
 * Someone there came to do one thing, and a card in the corner about storage is
 * in the way of it. A signed-in person is counted against their account
 * whichever they answer (`/privacy`, "While you are signed in"), so the answer
 * changes little there anyway. Not asking keeps a page cookieless, so a section
 * missing from this list costs the question and nothing more.
 *
 * Matched by first path segment, so a new post or docs page asks without being
 * listed, and a new top-level route stays quiet until somebody decides.
 */
export const ASKING_SECTIONS: ReadonlySet<string> = new Set([
    "", // the landing page
    "about",
    "blog",
    "changelog",
    "characters",
    "contact",
    "docs",
    "download",
    "privacy",
    "product",
    "templates",
    "tos",
    // Where a stranger is sent: a shared file, a share link, a template's install page.
    "d",
    "s",
    "import",
]);

export function asksForConsent(pathname: string): boolean {
    return ASKING_SECTIONS.has(pathname.split("/").filter(Boolean)[0] ?? "");
}

export function recordConsentDecision(
    decision: Exclude<ConsentDecision, "unanswered">,
): void {
    if (typeof window === "undefined") return;
    sessionDecision = decision;
    try {
        window.localStorage.setItem(STORAGE_KEY, decision);
    } catch {
        // Nothing to do: the decision holds for this session either way, and a
        // device that cannot remember it stays cookieless, which is the safe end.
    }
    for (const listener of listeners) listener();
}

/** Subscribers for `useSyncExternalStore`.
 *
 *  The decision is a client-only value — it lives in localStorage, which does
 *  not exist during SSR — so components read it through the store rather than
 *  by setting state in an effect. Same shape as `lib/desktop/local-capabilities`. */
const listeners = new Set<() => void>();

export function subscribeToConsent(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

/** The server never has a decision, so it always renders as if unanswered — and
 *  the banner is hidden until hydration tells it otherwise. */
export function consentServerSnapshot(): ConsentDecision {
    return "unanswered";
}
