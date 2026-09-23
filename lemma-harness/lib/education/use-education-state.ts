'use client';

import { useCallback, useSyncExternalStore } from 'react';

import {
    getServerEducationState,
    markEducation,
    readEducationState,
    subscribeEducation,
    type EducationValue,
} from '@/lib/education/education-store';

const noopSubscribe = () => () => {};

/** False during SSR and hydration, true after — avoids hydration mismatch for storage-driven UI. */
function useHydrated() {
    return useSyncExternalStore(noopSubscribe, () => true, () => false);
}

export function useEducationState() {
    return useSyncExternalStore(
        subscribeEducation,
        readEducationState,
        getServerEducationState
    );
}

export function useEducationKey(key: string): EducationValue | undefined {
    return useEducationState()[key];
}

export function useMarkEducation() {
    return useCallback((key: string, value: EducationValue = {}) => {
        markEducation(key, { ...value, markedAt: new Date().toISOString() });
    }, []);
}

/**
 * Visibility for a first-visit section primer: shown until dismissed, hidden
 * during SSR/hydration so the server and first client render agree.
 */
export function useSectionPrimer(key: string) {
    const seen = useEducationKey(key) !== undefined;
    const hydrated = useHydrated();
    const markEducationState = useMarkEducation();

    const dismiss = useCallback(() => {
        markEducationState(key);
    }, [key, markEducationState]);

    return { visible: hydrated && !seen, dismiss };
}
