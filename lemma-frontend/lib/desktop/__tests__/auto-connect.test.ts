import { afterEach, describe, expect, it } from 'vitest';

import {
    __attemptGuardForTests as guard,
    resetAutoConnectForTests,
    retryAutoConnect,
} from '@/lib/desktop/auto-connect';

const WORKSPACE = 'https://asur.work';
const OTHER = 'http://app.lemma.localhost:56608';

afterEach(() => {
    resetAutoConnectForTests();
});

// The bug this file exists for: the guard against connecting twice was a
// `useRef`, so it was per mount. Two components on one page call the hook —
// `ThisComputerCard` and the setup banner, which is on every page that has a
// card — and both mounted, both saw "not paired here" in the same commit, and
// both minted a pairing code. One machine arrived in the workspace twice, and
// because the host keeps one target per workspace URL the first of the two was
// orphaned and sat there permanently offline.
describe('the one-attempt guard', () => {
    it('is shared across every component that asks, not held per mount', () => {
        expect(guard.claim(WORKSPACE)).toBe(true);
        expect(guard.claim(WORKSPACE)).toBe(false);
    });

    it('is per workspace, so opening a second one still connects this machine', () => {
        // A Mac paired to its own local stack and then opened against a hosted
        // workspace genuinely needs a second pairing. The guard must not read
        // "already tried" as "already paired".
        expect(guard.claim(WORKSPACE)).toBe(true);
        expect(guard.claim(OTHER)).toBe(true);
    });

    it('records why the last attempt failed, and clears it on a later success', () => {
        guard.claim(WORKSPACE);
        guard.fail(new Error('Pairing code was rejected'), WORKSPACE);
        expect(guard.failure()).toBe('Pairing code was rejected');

        guard.succeed(WORKSPACE);
        expect(guard.failure()).toBeNull();
    });

    it('stringifies a rejection that is not an Error', () => {
        guard.fail('network down', WORKSPACE);
        expect(guard.failure()).toBe('network down');
    });

    it('lets a person ask again after a failure', () => {
        // The guard exists so a machine that cannot pair does not mint pairing
        // codes in a loop. It is not a reason to refuse someone who pressed a
        // button, and without this the only way back was reloading the page.
        guard.claim(WORKSPACE);
        guard.fail(new Error('offline'), WORKSPACE);
        expect(guard.claim(WORKSPACE)).toBe(false);

        retryAutoConnect();

        expect(guard.failure()).toBeNull();
        expect(guard.claim(WORKSPACE)).toBe(true);
    });

    it('tells subscribers when the failure changes, so the card can re-render', () => {
        let notified = 0;
        const unsubscribe = guard.subscribe(() => {
            notified += 1;
        });

        guard.fail(new Error('offline'), WORKSPACE);
        expect(notified).toBe(1);
        guard.succeed(WORKSPACE);
        expect(notified).toBe(2);
        // Nothing changed, so nothing is announced: this drives a render.
        guard.succeed(WORKSPACE);
        expect(notified).toBe(2);

        unsubscribe();
        guard.fail(new Error('offline again'), WORKSPACE);
        expect(notified).toBe(2);
    });
});
