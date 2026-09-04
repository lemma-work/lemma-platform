/**
 * Onboarding and activation emitters.
 *
 * Two reasons this is a module rather than `captureEvent` calls scattered at the
 * call sites. Elapsed-since-signup is bucketed in exactly one place, so two call
 * sites cannot disagree about what "fast" means. And the transitions below are
 * once-per-account facts: firing `activation.app_opened` on every app page view
 * would turn an activation metric into a usage metric, which is the failure mode
 * that makes an activation dashboard quietly useless.
 */

import { captureEvent } from "./client";

/**
 * How long after signup something happened, in bands.
 *
 * Bucketed rather than exact because the catalog admits ids, bounded enums and
 * booleans — and because the only question anyone asks of this number is which
 * band it lands in.
 */
export type ElapsedBucket =
    | "under_1m"
    | "1_5m"
    | "5_15m"
    | "15_60m"
    | "over_1h"
    | "unknown";

/** How this account arrived at its first pod. */
export type OnboardingEntryKind = "invite" | "domain_join" | "new_org";

export function elapsedBucket(signupAt?: string | null): ElapsedBucket {
    if (!signupAt) return "unknown";
    const started = Date.parse(signupAt);
    if (!Number.isFinite(started)) return "unknown";

    const minutes = (Date.now() - started) / 60_000;
    // A clock skewed backwards would otherwise report "under_1m" and quietly
    // flatter every funnel it touches.
    if (minutes < 0) return "unknown";
    if (minutes < 1) return "under_1m";
    if (minutes < 5) return "1_5m";
    if (minutes < 15) return "5_15m";
    if (minutes < 60) return "15_60m";
    return "over_1h";
}

export function trackOnboardingStep(step: string): void {
    captureEvent("onboarding.step_viewed", { step });
}

export function trackPodReady(
    entryKind: OnboardingEntryKind,
    signupAt?: string | null,
): void {
    if (!markOnce("pod_ready")) return;
    captureEvent("onboarding.pod_ready", {
        entry_kind: entryKind,
        elapsed_bucket: elapsedBucket(signupAt),
    });
}

export function trackSurfaceConnected(
    platform: string,
    signupAt?: string | null,
): void {
    if (!markOnce(`surface_connected:${platform}`)) return;
    captureEvent("activation.surface_connected", {
        platform,
        elapsed_bucket: elapsedBucket(signupAt),
    });
}

export function trackAppOpened(signupAt?: string | null): void {
    if (!markOnce("app_opened")) return;
    captureEvent("activation.app_opened", { elapsed_bucket: elapsedBucket(signupAt) });
}

export function trackMemberJoined(signupAt?: string | null): void {
    if (!markOnce("member_joined")) return;
    captureEvent("activation.member_joined", { elapsed_bucket: elapsedBucket(signupAt) });
}

const ONCE_KEY_PREFIX = "lemma:activation:";

/**
 * True the first time this browser reports a given transition.
 *
 * Deliberately localStorage and not memory: these fire on page load paths, so an
 * in-memory guard would let a refresh re-report the same first-ever moment. It is
 * a best-effort de-dup — a second browser reports again — which is why the real
 * answer still lives in the warehouse, keyed by person.
 */
function markOnce(key: string): boolean {
    if (typeof window === "undefined") return false;
    const storageKey = `${ONCE_KEY_PREFIX}${key}`;
    try {
        if (window.localStorage.getItem(storageKey)) return false;
        window.localStorage.setItem(storageKey, "1");
        return true;
    } catch {
        // Private mode, or storage disabled. Reporting twice is a better failure
        // than reporting never.
        return true;
    }
}
