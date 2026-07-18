import { play, setEnabled, type SoundName } from 'cuelume';

export type SoundFeedbackPreference = 'important' | 'all' | 'off';

export type SoundFeedbackEvent =
    | 'work-start'
    | 'work-complete'
    | 'work-fail'
    | 'work-waiting'
    | 'toggle-change'
    | 'action-success'
    | 'agent-open'
    | 'load-failure';

type SoundFeedbackLevel = Exclude<SoundFeedbackPreference, 'off'>;

export type SoundFeedbackEventConfig = {
    sound: SoundName;
    level: SoundFeedbackLevel;
    minGapMs: number;
};

export const SOUND_FEEDBACK_STORAGE_KEY = 'lemma:sound-feedback';
export const DEFAULT_SOUND_FEEDBACK_PREFERENCE: SoundFeedbackPreference = 'important';

export const SOUND_FEEDBACK_EVENTS: Record<SoundFeedbackEvent, SoundFeedbackEventConfig> = {
    'work-start': { sound: 'loading', level: 'important', minGapMs: 450 },
    'work-complete': { sound: 'bloom', level: 'important', minGapMs: 750 },
    'work-fail': { sound: 'error', level: 'important', minGapMs: 600 },
    'work-waiting': { sound: 'chime', level: 'important', minGapMs: 900 },
    'toggle-change': { sound: 'toggle', level: 'all', minGapMs: 100 },
    'action-success': { sound: 'release', level: 'all', minGapMs: 180 },
    'agent-open': { sound: 'ready', level: 'important', minGapMs: 600 },
    'load-failure': { sound: 'error', level: 'important', minGapMs: 600 },
};

const preferenceListeners = new Set<() => void>();
const lastPlayedAtByEvent = new Map<SoundFeedbackEvent, number>();
const playedOnceKeys = new Map<string, number>();
let inMemoryPreference: SoundFeedbackPreference | null = null;

function isSoundFeedbackPreference(value: unknown): value is SoundFeedbackPreference {
    return value === 'important' || value === 'all' || value === 'off';
}

function readStoredPreference(): SoundFeedbackPreference {
    if (typeof window === 'undefined') return DEFAULT_SOUND_FEEDBACK_PREFERENCE;

    try {
        const stored = window.localStorage.getItem(SOUND_FEEDBACK_STORAGE_KEY);
        return isSoundFeedbackPreference(stored) ? stored : DEFAULT_SOUND_FEEDBACK_PREFERENCE;
    } catch {
        return DEFAULT_SOUND_FEEDBACK_PREFERENCE;
    }
}

export function getSoundFeedbackPreference(): SoundFeedbackPreference {
    if (inMemoryPreference === null) {
        inMemoryPreference = readStoredPreference();
    }
    return inMemoryPreference;
}

export function getServerSoundFeedbackPreference(): SoundFeedbackPreference {
    return DEFAULT_SOUND_FEEDBACK_PREFERENCE;
}

export function setSoundFeedbackPreference(preference: SoundFeedbackPreference) {
    inMemoryPreference = preference;
    setEnabled(preference !== 'off');

    if (typeof window !== 'undefined') {
        try {
            window.localStorage.setItem(SOUND_FEEDBACK_STORAGE_KEY, preference);
        } catch {
            // Keep the in-memory preference when storage is unavailable.
        }
    }

    preferenceListeners.forEach((listener) => listener());
}

export function subscribeSoundFeedbackPreference(listener: () => void) {
    preferenceListeners.add(listener);

    const handleStorage = (event: StorageEvent) => {
        if (event.key !== SOUND_FEEDBACK_STORAGE_KEY) return;
        inMemoryPreference = readStoredPreference();
        setEnabled(inMemoryPreference !== 'off');
        listener();
    };

    if (typeof window !== 'undefined') {
        window.addEventListener('storage', handleStorage);
    }

    return () => {
        preferenceListeners.delete(listener);
        if (typeof window !== 'undefined') {
            window.removeEventListener('storage', handleStorage);
        }
    };
}

export function isSoundFeedbackEventEnabled(
    event: SoundFeedbackEvent,
    preference: SoundFeedbackPreference,
) {
    if (preference === 'off') return false;
    return preference === 'all' || SOUND_FEEDBACK_EVENTS[event].level === 'important';
}

export function playSoundFeedback(
    event: SoundFeedbackEvent,
    options: { onceKey?: string; now?: number } = {},
) {
    const preference = getSoundFeedbackPreference();
    if (!isSoundFeedbackEventEnabled(event, preference)) return false;

    const now = options.now ?? Date.now();
    const config = SOUND_FEEDBACK_EVENTS[event];
    const lastPlayedAt = lastPlayedAtByEvent.get(event) ?? 0;
    if (now - lastPlayedAt < config.minGapMs) return false;

    if (options.onceKey) {
        const key = `${event}:${options.onceKey}`;
        if (playedOnceKeys.has(key)) return false;
        playedOnceKeys.set(key, now);

        if (playedOnceKeys.size > 300) {
            const oldestKey = playedOnceKeys.keys().next().value;
            if (oldestKey) playedOnceKeys.delete(oldestKey);
        }
    }

    lastPlayedAtByEvent.set(event, now);

    try {
        play(config.sound);
        return true;
    } catch {
        // Sound is enhancement-only; it must never interrupt the user action.
        return false;
    }
}

export function resetSoundFeedbackForTests() {
    inMemoryPreference = null;
    lastPlayedAtByEvent.clear();
    playedOnceKeys.clear();
}
