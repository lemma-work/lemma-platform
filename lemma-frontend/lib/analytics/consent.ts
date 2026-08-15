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
    const raw = readStorage();
    if (raw === "granted" || raw === "denied") return raw;
    return "unanswered";
}

export function hasAnalyticsConsent(): boolean {
    return readConsentDecision() === "granted";
}

export function recordConsentDecision(decision: Exclude<ConsentDecision, "unanswered">): void {
    if (typeof window === "undefined") return;
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
