// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DRAFT_PERSIST_DEBOUNCE_MS, draftStorageKey, useDraftPersistence } from './use-assistant-experience';

const NEW_CHAT = draftStorageKey(null);

// The composer's own wiring: `setDraft` moves the value, and the hook is told
// about it on the next render. Driving both by hand is what lets a test put a
// conversation change inside the debounce window, which is where the bug was.
function mountComposer(initial: { id: string | null; draft: string }) {
    const setDraft = vi.fn();
    const view = renderHook(
        ({ id, draft }: { id: string | null; draft: string }) => useDraftPersistence(id, draft, setDraft),
        { initialProps: initial },
    );
    return { ...view, setDraft };
}

beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
});

afterEach(() => {
    vi.useRealTimers();
});

describe('useDraftPersistence', () => {
    it('keeps a draft that survived the debounce', () => {
        const { rerender } = mountComposer({ id: null, draft: '' });

        act(() => { rerender({ id: null, draft: 'half a thought' }); });
        expect(localStorage.getItem(NEW_CHAT)).toBeNull();

        act(() => { vi.advanceTimersByTime(DRAFT_PERSIST_DEBOUNCE_MS); });
        expect(localStorage.getItem(NEW_CHAT)).toBe('half a thought');
    });

    it('does not put a sent message back in the next new chat', () => {
        const { result, rerender } = mountComposer({ id: null, draft: '' });

        act(() => { rerender({ id: null, draft: 'ship it' }); });
        act(() => { vi.advanceTimersByTime(DRAFT_PERSIST_DEBOUNCE_MS); });
        expect(localStorage.getItem(NEW_CHAT)).toBe('ship it');

        // Send: the composer empties, and the server answers with the id of the
        // conversation it just created — sooner than the debounce, on any
        // network worth having.
        act(() => {
            result.current();
            rerender({ id: null, draft: '' });
        });
        act(() => { vi.advanceTimersByTime(DRAFT_PERSIST_DEBOUNCE_MS / 4); });
        act(() => { rerender({ id: 'created', draft: '' }); });

        expect(localStorage.getItem(NEW_CHAT)).toBeNull();
    });

    it('hands a conversation its unflushed draft when the composer moves on', () => {
        const { rerender } = mountComposer({ id: 'first', draft: '' });

        act(() => { rerender({ id: 'first', draft: 'mid-sentence' }); });
        act(() => { vi.advanceTimersByTime(DRAFT_PERSIST_DEBOUNCE_MS / 4); });
        act(() => { rerender({ id: 'second', draft: 'mid-sentence' }); });

        expect(localStorage.getItem(draftStorageKey('first'))).toBe('mid-sentence');
        expect(localStorage.getItem(draftStorageKey('second'))).toBeNull();
    });

    it('flushes an unfinished draft when the composer unmounts', () => {
        const { rerender, unmount } = mountComposer({ id: 'first', draft: '' });

        act(() => { rerender({ id: 'first', draft: 'unfinished' }); });
        act(() => { unmount(); });

        expect(localStorage.getItem(draftStorageKey('first'))).toBe('unfinished');
    });

    it('restores the draft the conversation was left with, and only that one', () => {
        localStorage.setItem(draftStorageKey('second'), 'left here');
        const { rerender, setDraft } = mountComposer({ id: 'first', draft: '' });
        setDraft.mockClear();

        act(() => { rerender({ id: 'second', draft: '' }); });
        expect(setDraft).toHaveBeenLastCalledWith('left here');

        act(() => { rerender({ id: null, draft: 'left here' }); });
        expect(setDraft).toHaveBeenLastCalledWith('');
    });

    it('leaves a stored draft alone when the composer merely mounts', () => {
        localStorage.setItem(NEW_CHAT, 'from a previous tab');
        const { unmount } = mountComposer({ id: null, draft: '' });

        act(() => { unmount(); });

        expect(localStorage.getItem(NEW_CHAT)).toBe('from a previous tab');
    });
});
