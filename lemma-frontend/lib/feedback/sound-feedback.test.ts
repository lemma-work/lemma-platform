import { describe, expect, it } from 'vitest';

import {
    SOUND_FEEDBACK_EVENTS,
    isSoundFeedbackEventEnabled,
} from './sound-feedback';

describe('sound feedback event map', () => {
    it('uses the agreed semantic cue for every product event', () => {
        expect(SOUND_FEEDBACK_EVENTS).toMatchObject({
            'work-start': { sound: 'loading' },
            'work-complete': { sound: 'bloom' },
            'work-fail': { sound: 'error' },
            'work-waiting': { sound: 'chime' },
            'toggle-change': { sound: 'toggle' },
            'action-success': { sound: 'release' },
            'agent-open': { sound: 'ready' },
            'load-failure': { sound: 'error' },
        });
    });

    it('keeps micro feedback out of the Important only preference', () => {
        expect(isSoundFeedbackEventEnabled('work-complete', 'important')).toBe(true);
        expect(isSoundFeedbackEventEnabled('toggle-change', 'important')).toBe(false);
        expect(isSoundFeedbackEventEnabled('action-success', 'important')).toBe(false);
    });

    it('enables every event for All feedback and none for Off', () => {
        for (const event of Object.keys(SOUND_FEEDBACK_EVENTS) as Array<keyof typeof SOUND_FEEDBACK_EVENTS>) {
            expect(isSoundFeedbackEventEnabled(event, 'all')).toBe(true);
            expect(isSoundFeedbackEventEnabled(event, 'off')).toBe(false);
        }
    });
});
