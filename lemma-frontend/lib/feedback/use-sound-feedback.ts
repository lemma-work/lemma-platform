'use client';

import { useSyncExternalStore } from 'react';

import {
    getServerSoundFeedbackPreference,
    getSoundFeedbackPreference,
    subscribeSoundFeedbackPreference,
} from './sound-feedback';

export function useSoundFeedbackPreference() {
    return useSyncExternalStore(
        subscribeSoundFeedbackPreference,
        getSoundFeedbackPreference,
        getServerSoundFeedbackPreference,
    );
}
