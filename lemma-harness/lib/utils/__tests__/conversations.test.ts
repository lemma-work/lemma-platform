import { describe, expect, it } from 'vitest';

import { getConversationSignal } from '@/lib/utils/conversations';

const NOW = new Date('2026-08-04T12:00:00.000Z').getTime();
const MINUTES = 60 * 1000;

describe('getConversationSignal', () => {
    it('lights and animates work that is in flight', () => {
        const signal = getConversationSignal({ status: 'running' }, NOW);

        expect(signal.tone).toBe('live');
        expect(signal.filled).toBe(true);
        expect(signal.pulse).toBe(true);
        expect(signal.label).toBe('Working');
    });

    it('lights a conversation that is stopped on you, without animating it', () => {
        const signal = getConversationSignal({ status: 'waiting' }, NOW);

        expect(signal.tone).toBe('warning');
        expect(signal.filled).toBe(true);
        expect(signal.pulse).toBe(false);
    });

    it('reads a stop in progress as stopping rather than as awaiting input', () => {
        expect(getConversationSignal({ status: 'stop_requested' }, NOW).label).toBe('Stopping');
    });

    it.each(['completed', 'stopped', 'unknown-to-us', null, undefined])(
        'rests on %s, which is a fact about the last run and not a thing to do',
        (status) => {
            const signal = getConversationSignal({ status }, NOW);

            expect(signal.tone).toBe('none');
            expect(signal.filled).toBe(false);
            expect(signal.pulse).toBe(false);
            expect(signal.label).toBeNull();
        },
    );

    it('lights a failure that is still news', () => {
        const signal = getConversationSignal({
            status: 'failed',
            last_run_finished_at: new Date(NOW - 5 * MINUTES).toISOString(),
        }, NOW);

        expect(signal.tone).toBe('danger');
        expect(signal.filled).toBe(true);
        expect(signal.pulse).toBe(false);
    });

    it('rests a failure old enough that nobody is about to act on it', () => {
        const signal = getConversationSignal({
            status: 'failed',
            last_run_finished_at: new Date(NOW - 90 * MINUTES).toISOString(),
        }, NOW);

        expect(signal.tone).toBe('none');
    });

    it.each([null, undefined, 'not a date'])(
        'rests a failure timestamped %s rather than marking it red forever',
        (last_run_finished_at) => {
            const signal = getConversationSignal({ status: 'failed', last_run_finished_at }, NOW);

            expect(signal.tone).toBe('none');
        },
    );
});
