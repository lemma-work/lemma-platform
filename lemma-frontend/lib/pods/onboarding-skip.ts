export const ONBOARDING_SKIPPED_FIRST_POD_KEY = 'lemma:onboarding-skipped-first-pod';

export function readOnboardingSkippedFirstPod(): string | null {
    if (typeof window === 'undefined') return null;

    try {
        return window.localStorage.getItem(ONBOARDING_SKIPPED_FIRST_POD_KEY);
    } catch {
        return null;
    }
}

export function subscribeToOnboardingSkippedFirstPod(callback: () => void) {
    if (typeof window === 'undefined') return () => undefined;

    window.addEventListener('storage', callback);
    return () => window.removeEventListener('storage', callback);
}

export function markOnboardingSkippedFirstPod() {
    if (typeof window === 'undefined') return;

    try {
        window.localStorage.setItem(ONBOARDING_SKIPPED_FIRST_POD_KEY, '1');
        window.dispatchEvent(new StorageEvent('storage', { key: ONBOARDING_SKIPPED_FIRST_POD_KEY }));
    } catch {
        // localStorage can be unavailable in private or restricted browser contexts.
    }
}

export function clearOnboardingSkippedFirstPod() {
    if (typeof window === 'undefined') return;

    try {
        window.localStorage.removeItem(ONBOARDING_SKIPPED_FIRST_POD_KEY);
        window.dispatchEvent(new StorageEvent('storage', { key: ONBOARDING_SKIPPED_FIRST_POD_KEY }));
    } catch {
        // localStorage can be unavailable in private or restricted browser contexts.
    }
}
