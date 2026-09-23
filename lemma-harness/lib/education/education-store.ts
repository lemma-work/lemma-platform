export type EducationValue = Record<string, unknown>;
export type EducationStateMap = Record<string, EducationValue>;

export const EDUCATION_STORAGE_KEY = 'lemma:education:v1';

const EMPTY_STATE: EducationStateMap = {};

let cachedState: EducationStateMap | null = null;
const listeners = new Set<() => void>();

function notify() {
    for (const listener of listeners) listener();
}

function parseState(raw: string | null): EducationStateMap {
    if (!raw) return EMPTY_STATE;
    try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            return parsed as EducationStateMap;
        }
    } catch {
        // Corrupt blob — treat as nothing seen rather than breaking the app.
    }
    return EMPTY_STATE;
}

export function readEducationState(): EducationStateMap {
    if (typeof window === 'undefined') return EMPTY_STATE;
    if (cachedState !== null) return cachedState;
    try {
        cachedState = parseState(window.localStorage.getItem(EDUCATION_STORAGE_KEY));
    } catch {
        cachedState = EMPTY_STATE;
    }
    return cachedState;
}

export function getServerEducationState(): EducationStateMap {
    return EMPTY_STATE;
}

export function markEducation(key: string, value: EducationValue = {}): void {
    if (typeof window === 'undefined') return;
    const next = { ...readEducationState(), [key]: value };
    cachedState = next;
    try {
        window.localStorage.setItem(EDUCATION_STORAGE_KEY, JSON.stringify(next));
    } catch {
        // Restricted storage — keep the in-memory state for this page lifetime.
    }
    notify();
}

export function resetEducation(): void {
    if (typeof window === 'undefined') return;
    cachedState = EMPTY_STATE;
    try {
        window.localStorage.removeItem(EDUCATION_STORAGE_KEY);
    } catch {
        // Ignore restricted storage.
    }
    notify();
}

export function removeEducationKeysWithPrefix(prefix: string): void {
    if (typeof window === 'undefined') return;
    const current = readEducationState();
    const next: EducationStateMap = {};
    for (const [key, value] of Object.entries(current)) {
        if (!key.startsWith(prefix)) next[key] = value;
    }
    cachedState = next;
    try {
        window.localStorage.setItem(EDUCATION_STORAGE_KEY, JSON.stringify(next));
    } catch {
        // Restricted storage — keep the in-memory state for this page lifetime.
    }
    notify();
}

export function subscribeEducation(listener: () => void): () => void {
    listeners.add(listener);
    if (listeners.size === 1 && typeof window !== 'undefined') {
        window.addEventListener('storage', handleStorageEvent);
    }
    return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && typeof window !== 'undefined') {
            window.removeEventListener('storage', handleStorageEvent);
        }
    };
}

function handleStorageEvent(event: StorageEvent) {
    if (event.key !== null && event.key !== EDUCATION_STORAGE_KEY) return;
    cachedState = parseState(event.newValue);
    notify();
}

