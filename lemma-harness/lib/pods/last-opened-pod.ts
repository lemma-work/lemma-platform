export const LAST_OPENED_POD_STORAGE_KEY = 'lemma:last-opened-pod-id';

export function readLastOpenedPodId() {
    if (typeof window === 'undefined') return null;

    try {
        return window.localStorage.getItem(LAST_OPENED_POD_STORAGE_KEY);
    } catch {
        return null;
    }
}

export function subscribeToLastOpenedPodId(callback: () => void) {
    if (typeof window === 'undefined') return () => undefined;

    window.addEventListener('storage', callback);
    return () => window.removeEventListener('storage', callback);
}

export function writeLastOpenedPodId(podId: string) {
    if (typeof window === 'undefined' || !podId) return;

    try {
        window.localStorage.setItem(LAST_OPENED_POD_STORAGE_KEY, podId);
    } catch {
        // localStorage can be unavailable in private or restricted browser contexts.
    }
}

export function clearLastOpenedPodId() {
    if (typeof window === 'undefined') return;

    try {
        window.localStorage.removeItem(LAST_OPENED_POD_STORAGE_KEY);
    } catch {
        // localStorage can be unavailable in private or restricted browser contexts.
    }
}
