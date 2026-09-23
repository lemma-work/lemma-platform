import { describe, expect, it } from 'vitest';
import { composerActionState } from './action-state';

const base = {
    hasDraft: false,
    hasAttachments: false,
    disabled: false,
    isBusy: false,
    busyAcceptsSend: false,
    canStop: false,
};

describe('pod home — a surface that refuses a send while busy', () => {
    // No `onStop`, and its submit handler returns early on `isBusy`. An enabled
    // Send here is a button that does nothing.
    const home = { ...base, canStop: false, busyAcceptsSend: false };

    it('offers Send with a draft', () => {
        expect(composerActionState({ ...home, hasDraft: true }))
            .toEqual({ canSend: true, showStop: false });
    });

    it('withholds Send while busy, rather than enabling a no-op', () => {
        expect(composerActionState({ ...home, hasDraft: true, isBusy: true }))
            .toEqual({ canSend: false, showStop: false });
    });

    it('has no Stop to fall back to, so stays plain-disabled while busy', () => {
        expect(composerActionState({ ...home, isBusy: true }))
            .toEqual({ canSend: false, showStop: false });
    });

    it('sends staged files with no text', () => {
        expect(composerActionState({ ...home, hasAttachments: true }).canSend).toBe(true);
    });
});

describe('the assistant — a surface that takes a follow-up mid-run', () => {
    const assistant = { ...base, canStop: true, busyAcceptsSend: true };

    it('shows Stop while a run works and there is nothing to send', () => {
        expect(composerActionState({ ...assistant, isBusy: true }))
            .toEqual({ canSend: false, showStop: true });
    });

    it('turns the primary into Send the moment there is a draft', () => {
        expect(composerActionState({ ...assistant, isBusy: true, hasDraft: true }))
            .toEqual({ canSend: true, showStop: false });
    });

    it('does the same for a staged file with no text', () => {
        expect(composerActionState({ ...assistant, isBusy: true, hasAttachments: true }))
            .toEqual({ canSend: true, showStop: false });
    });

    it('returns to Stop when the draft is cleared', () => {
        const typed = composerActionState({ ...assistant, isBusy: true, hasDraft: true });
        const cleared = composerActionState({ ...assistant, isBusy: true, hasDraft: false });
        expect(typed.showStop).toBe(false);
        expect(cleared.showStop).toBe(true);
    });

    it('offers Send normally when nothing is running', () => {
        expect(composerActionState({ ...assistant, hasDraft: true }))
            .toEqual({ canSend: true, showStop: false });
    });
});

describe('disabled beats everything', () => {
    // `disabled` is no write access, or an approval card waiting on the person.
    it('withholds Send even with a draft on a surface that takes follow-ups', () => {
        expect(composerActionState({
            ...base, canStop: true, busyAcceptsSend: true,
            hasDraft: true, isBusy: true, disabled: true,
        })).toEqual({ canSend: false, showStop: true });
    });

    it('leaves Stop reachable, because stopping is not writing', () => {
        expect(composerActionState({
            ...base, canStop: true, isBusy: true, disabled: true,
        }).showStop).toBe(true);
    });
});
