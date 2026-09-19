// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';

import { usePictureInPicture } from '@/lib/hooks/use-picture-in-picture';

/**
 * A real OS window, and the four ways one goes wrong.
 *
 * None of this is reachable from the card's own test: Document
 * Picture-in-Picture does not exist in jsdom, and it does not exist in
 * Firefox or Safari either, so `supported` is the load-bearing part rather
 * than a nicety. The window is also a document of its own, which is why the
 * stylesheet copy exists and why a `<link>` goes across by reference while
 * an inline `<style>` goes across by text -- reading rules out of a
 * cross-origin sheet throws.
 */

const fakePipWindow = () => {
    const doc = document.implementation.createHTMLDocument('pip');
    const listeners: Record<string, Array<() => void>> = {};
    return {
        document: doc,
        closed: false,
        close: vi.fn(function (this: { closed: boolean }) {
            this.closed = true;
        }),
        addEventListener: (name: string, fn: () => void) => {
            (listeners[name] ||= []).push(fn);
        },
        fire: (name: string) => (listeners[name] || []).forEach((fn) => fn()),
    };
};

const withApi = (requestWindow: (options?: unknown) => Promise<unknown>) => {
    Object.defineProperty(window, 'documentPictureInPicture', {
        value: { requestWindow },
        configurable: true,
        writable: true,
    });
};

afterEach(() => {
    cleanup();
    Reflect.deleteProperty(
        window as unknown as Record<string, unknown>,
        'documentPictureInPicture',
    );
    document.head.querySelectorAll('[data-test-sheet]').forEach((n) => n.remove());
});

describe('usePictureInPicture', () => {
    it('reports unsupported where the API is absent', async () => {
        // Firefox and Safari, and every server render. A control that offers
        // this has to hide itself, so a wrong `true` here is a dead button.
        const { result } = renderHook(() => usePictureInPicture());

        await waitFor(() => expect(result.current.supported).toBe(false));
    });

    it('reports supported only after mount, never on the first render', async () => {
        // The hydration guard, and the reason `supported` is state behind an
        // effect rather than a plain `api() !== null`. The server has no
        // `window`, so the first client render has to agree with it and say
        // no whatever the browser can do -- which means sampling the *first*
        // render, not the settled value. `renderHook` flushes effects before
        // it returns, so `result.current` is already too late to see it.
        withApi(async () => fakePipWindow());
        const perRender: boolean[] = [];

        const { result } = renderHook(() => {
            const hook = usePictureInPicture();
            perRender.push(hook.supported);
            return hook;
        });

        expect(perRender[0]).toBe(false);
        await waitFor(() => expect(result.current.supported).toBe(true));
    });

    it('carries an inline stylesheet across by text, and dresses the body', async () => {
        // Only the inline half is assertable here: jsdom does not fetch a
        // `<link>`, so it never becomes a `document.styleSheets` entry and
        // the by-reference branch has nothing to walk. Asserting the branch
        // that *is* reachable beats a test shaped like both and exercising
        // neither. The window is its own document, so without this copy it
        // renders unstyled.
        const inline = document.createElement('style');
        inline.textContent = '.pane { color: red }';
        inline.dataset.testSheet = '';
        document.head.append(inline);

        const created = fakePipWindow();
        withApi(async () => created);
        const { result } = renderHook(() => usePictureInPicture());

        await act(async () => {
            await result.current.open();
        });

        const head = created.document.head;
        expect(head.querySelector('style')?.textContent).toBe('.pane { color: red }');
        // The body's own background, or the window flashes white before the
        // canvas paints.
        expect(created.document.body.style.margin).toBe('0px');
    });

    it('opens one window even when asked twice before the first resolves', async () => {
        // The guard is a ref rather than state precisely so it holds inside a
        // single tick; two OS windows for two clicks is the bug it prevents.
        let opens = 0;
        withApi(async () => {
            opens += 1;
            await new Promise((resolve) => setTimeout(resolve, 5));
            return fakePipWindow();
        });
        const { result } = renderHook(() => usePictureInPicture());

        await act(async () => {
            await Promise.all([result.current.open(), result.current.open()]);
        });

        expect(opens).toBe(1);
    });

    it('forgets the window when the person closes it', async () => {
        // `pagehide`, not `unload`: Chromium does not fire `unload`
        // reliably, and a stale handle makes the control offer to close a
        // window that is already gone.
        const created = fakePipWindow();
        withApi(async () => created);
        const { result } = renderHook(() => usePictureInPicture());

        await act(async () => {
            await result.current.open();
        });
        expect(result.current.pipWindow).not.toBeNull();

        act(() => created.fire('pagehide'));

        await waitFor(() => expect(result.current.pipWindow).toBeNull());
    });

    it('is not left floating when the panel that opened it goes away', async () => {
        // A PiP window outlives its component. One showing a pane nobody can
        // reach any more is worse than no window at all.
        const created = fakePipWindow();
        withApi(async () => created);
        const { result, unmount } = renderHook(() => usePictureInPicture());

        await act(async () => {
            await result.current.open();
        });
        unmount();

        expect(created.close).toHaveBeenCalled();
    });

    it('stays quiet when the browser refuses the window', async () => {
        // No user gesture behind it, or the person dismissed it. The inline
        // view is still there and is what they were looking at, so there is
        // nothing to report and nothing to throw.
        withApi(async () => {
            throw new Error('NotAllowedError');
        });
        const { result } = renderHook(() => usePictureInPicture());

        await act(async () => {
            await expect(result.current.open()).resolves.toBeUndefined();
        });

        expect(result.current.pipWindow).toBeNull();
    });
});
