export const ONBOARDING_SKIPPED_FIRST_POD_KEY = 'lemma:onboarding-skipped-first-pod';
const ONBOARDING_SKIPPED_FIRST_POD_EVENT = 'lemma:onboarding-skipped-first-pod-change';

/**
 * Who skipped their first pod, not merely that somebody did.
 *
 * This used to store `"1"`. Browser storage is per browser and this app signs a
 * different person in whenever it is asked to, so a flag with no owner said
 * "this account already has somewhere to be" about every account that came
 * after — silently switching off first-pod provisioning for all of them, with
 * no expiry and no way to notice. It cost an afternoon.
 *
 * Storing the owner's email *as the value* makes the old `"1"` heal itself: it
 * matches nobody's address, so it reads as "not this account" and provisioning
 * runs again.
 */
export function normalizeSkipOwner(email?: string | null): string | null {
    const normalized = email?.trim().toLowerCase();
    return normalized || null;
}

/** The address that skipped, or null. Compare it against the signed-in user. */
export function readOnboardingSkippedFirstPod(): string | null {
    if (typeof window === 'undefined') return null;

    try {
        return window.localStorage.getItem(ONBOARDING_SKIPPED_FIRST_POD_KEY);
    } catch {
        return null;
    }
}

/** True only when *this* account is the one that skipped. */
export function hasSkippedFirstPod(
    stored: string | null,
    ownerEmail?: string | null,
): boolean {
    const owner = normalizeSkipOwner(ownerEmail);
    return Boolean(owner) && stored === owner;
}

export function subscribeToOnboardingSkippedFirstPod(callback: () => void) {
    if (typeof window === 'undefined') return () => undefined;

    window.addEventListener('storage', callback);
    window.addEventListener(ONBOARDING_SKIPPED_FIRST_POD_EVENT, callback);
    return () => {
        window.removeEventListener('storage', callback);
        window.removeEventListener(ONBOARDING_SKIPPED_FIRST_POD_EVENT, callback);
    };
}

export function markOnboardingSkippedFirstPod(ownerEmail?: string | null) {
    if (typeof window === 'undefined') return;

    const owner = normalizeSkipOwner(ownerEmail);
    // Nobody to attribute it to means nobody it can be read back for. Writing an
    // unowned flag is what caused the original bug.
    if (!owner) return;

    try {
        window.localStorage.setItem(ONBOARDING_SKIPPED_FIRST_POD_KEY, owner);
        window.dispatchEvent(new Event(ONBOARDING_SKIPPED_FIRST_POD_EVENT));
    } catch {
        // localStorage can be unavailable in private or restricted contexts.
    }
}

export function clearOnboardingSkippedFirstPod() {
    if (typeof window === 'undefined') return;

    try {
        window.localStorage.removeItem(ONBOARDING_SKIPPED_FIRST_POD_KEY);
        window.dispatchEvent(new Event(ONBOARDING_SKIPPED_FIRST_POD_EVENT));
    } catch {
        // localStorage can be unavailable in private or restricted contexts.
    }
}
