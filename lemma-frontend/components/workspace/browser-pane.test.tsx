// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserPane } from './browser-pane';

/**
 * A stand-in for the real `WebSocket` `browser-pane.tsx` now constructs
 * itself, purely so a test can decide what close code it reports. jsdom's own
 * `WebSocket` tries a real network connection, which has no server to answer
 * it in this environment and gives a test no way to choose a code anyway.
 */
class FakeSocket {
    url: string;
    private listeners: Record<string, Array<(event: { code: number }) => void>> = {};

    constructor(url: string) {
        this.url = url;
    }

    addEventListener(type: string, listener: (event: { code: number }) => void) {
        (this.listeners[type] ??= []).push(listener);
    }

    closeWith(code: number) {
        for (const listener of this.listeners.close ?? []) listener({ code });
    }
}
vi.stubGlobal('WebSocket', FakeSocket);

/**
 * jsdom has no `ResizeObserver`, and the pane uses one to ask the sandbox
 * display to match its own size.
 *
 * This used to exist only so constructing one did not throw, on the grounds
 * that what a resize *does* is a round trip belonging to the endpoint's own
 * tests. That reasoning missed the part that is genuinely this component's:
 * *when* it asks. The pane memoised the last size it requested and never
 * re-sent it on a reconnect, so after a sandbox resume the display came back
 * at its starting size and the picture stayed letterboxed with nothing to
 * un-stick it. No test could see that, because none of them ever connected
 * twice with an observer that fired.
 */
class FakeResizeObserver {
    // Same signature as the real constructor, and it keeps what it is handed.
    // A stub that took no callback would still work for the tests that never
    // fire it, but it would be a narrower contract than the thing it
    // replaces -- which is how a test comes to pass against a call the
    // browser would reject.
    readonly callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
        observers.push(this);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
}
const observers: FakeResizeObserver[] = [];
vi.stubGlobal('ResizeObserver', FakeResizeObserver);

/**
 * A stand-in for `@novnc/novnc`'s `RFB` class.
 *
 * `BrowserPane` owns none of the input-capture or rendering logic RFB does
 * for itself -- that is the whole point of moving to it -- so what is worth
 * testing here is the three things this component still does on its own:
 * which URL it opens RFB against, keeping `viewOnly` in sync with the
 * driving toggle, and the paste sequence (`clipboardPasteFrom` then a
 * synthetic Ctrl+V) that makes a real synced clipboard actually paste
 * something rather than merely holding it.
 */
const resized: string[] = [];
const rfbInstances = vi.hoisted(() => [] as FakeRfb[]);

class FakeRfb {
    target: Element;
    url: string;
    socket: FakeSocket | null = null;
    viewOnly = false;
    scaleViewport = false;
    background = '';
    sentKeys: Array<[number, string, boolean]> = [];
    clipboardWrites: string[] = [];
    private listeners: Record<string, Array<(event?: unknown) => void>> = {};

    constructor(target: Element, urlOrSocket: string | FakeSocket) {
        this.target = target;
        // `browser-pane.tsx` owns the `WebSocket` itself now (so it can read
        // the real close code -- see `closeCodeToState`), so this is what a
        // real RFB is actually handed. Kept, not just unwrapped, so a test
        // can drive its `closeWith` independently of RFB's own `disconnect`.
        if (typeof urlOrSocket === 'string') {
            this.url = urlOrSocket;
        } else {
            this.url = urlOrSocket.url;
            this.socket = urlOrSocket;
        }
        rfbInstances.push(this);
    }

    addEventListener(type: string, listener: (event?: unknown) => void) {
        (this.listeners[type] ??= []).push(listener);
    }

    // `event` is optional so the many `emit('connect')` callers stay as they
    // are; the real RFB hands its listeners a CustomEvent, and `clipboard`
    // is the one whose payload the pane actually reads.
    emit(type: string, event?: unknown) {
        for (const listener of this.listeners[type] ?? []) listener(event);
    }

    sendKey(keysym: number, code: string, down = true) {
        this.sentKeys.push([keysym, code, down]);
    }

    clipboardPasteFrom(text: string) {
        this.clipboardWrites.push(text);
    }

    disconnected = false;

    disconnect() {
        // Not a synchronous `emit('disconnect')`: the real RFB closes the
        // socket and reports the close asynchronously, after whatever called
        // `disconnect()` has moved on -- which is exactly the gap the
        // regression test below exercises. Callers that want the event
        // still call `emit('disconnect')` themselves, on their own schedule.
        this.disconnected = true;
    }
}

vi.mock('@novnc/novnc', () => ({ default: FakeRfb }));

// Where the fake browser says it is. `vi.hoisted` because `vi.mock` is
// hoisted above the imports and would otherwise close over an undefined name.
const page = vi.hoisted(() => ({ url: 'about:blank' }));
vi.mock('@/lib/sdk/lemma-client', async (importOriginal) => ({
    // Spread the real module: `vncSocketUrl` reaches for `getLemmaApiBaseUrl`
    // from here, and a mock that answers only what this file names breaks
    // every test in it rather than the one it meant to steer.
    ...(await importOriginal<typeof import('@/lib/sdk/lemma-client')>()),
    getLemmaClient: () => ({
        workspace: {
            browserCurrentPageUrl: async () => ({ url: page.url }),
            // Exercised by the pane's ResizeObserver; the display fitting is
            // not what these tests are about.
            browserResizeDisplay: async (width: number, height: number) => {
                resized.push(`${width}x${height}`);
                return { size: null };
            },
        },
    }),
}));

afterEach(() => {
    rfbInstances.length = 0;
    observers.length = 0;
    resized.length = 0;
    page.url = 'about:blank';
    cleanup();
});

const connect = async () => {
    await waitFor(() => expect(rfbInstances).toHaveLength(1));
    const rfb = rfbInstances[0];
    act(() => rfb.emit('connect'));
    return rfb;
};

describe('opening the view', () => {
    it('can be driven whether it is watching a run or showing a sign-in', async () => {
        // There is no watch-only pane. It existed because the relay took a
        // driving lease the moment a control socket opened, and for an
        // ordinary watch that is the agent's own session -- so an open panel
        // would have stopped the agent browsing. Opening read-only traded
        // that for a browser nobody could click, which is not a browser.
        // The lease is gone entirely: it was a no-op in the sign-in case it
        // was written for (a different session) and only ever cost the agent
        // its own browser, so the socket always carries input.
        render(<BrowserPane conversationId="conv-1" />);
        const watching = await connect();
        expect(watching.url).toContain('mode=control');
        expect(watching.viewOnly).toBe(false);

        cleanup();
        rfbInstances.length = 0;
        render(<BrowserPane origin="https://example.com" />);
        const driving = await connect();
        expect(driving.url).toContain('mode=control');
        expect(driving.viewOnly).toBe(false);
    });

    it('carries the origin, for the backend to steer the browser with', async () => {
        render(<BrowserPane origin="https://example.com" />);
        const rfb = await connect();
        expect(rfb.url).toContain('origin=https%3A%2F%2Fexample.com');
    });

    it('names no conversation or session when an origin steers a sign-in', async () => {
        // A sign-in names its own session; the conversation the pane happens
        // to be open in, if any, is not it.
        render(<BrowserPane origin="https://example.com" conversationId="conv-abc" />);
        const rfb = await connect();
        expect(rfb.url).not.toContain('conversation=');
        expect(rfb.url).not.toContain('session=');
    });

    it('carries the conversation for a plain watch/drive, with no origin', async () => {
        // `run_browser_script` runs every agent browser command in a session
        // named for the conversation, not the shared default -- without this,
        // the pane checked the wrong session and refused forever with "no
        // browser running" while the agent's browser was live the whole time.
        render(<BrowserPane conversationId="conv-abc" />);
        const rfb = await connect();
        expect(rfb.url).toContain('conversation=conv-abc');
    });

    it('reconnects to the new conversation when the mounted pane is handed a different one', async () => {
        // `ComputerPanel` is one long-lived instance reused across whichever
        // conversation is open -- the id is a prop, not a mount key -- so
        // switching conversations re-runs this effect on the same component
        // rather than making a fresh one. Without `conversationId` in the
        // effect's dependency array, the first conversation's socket would
        // stay open and a person switching conversations would keep watching
        // the old one's browser.
        const { rerender } = render(<BrowserPane conversationId="conv-first" />);
        const first = await connect();
        expect(first.url).toContain('conversation=conv-first');
        expect(first.disconnected).toBe(false);

        rerender(<BrowserPane conversationId="conv-second" />);
        await waitFor(() => expect(first.disconnected).toBe(true));
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
        const second = rfbInstances[1];
        expect(second.url).toContain('conversation=conv-second');
        expect(second.url).not.toContain('conv-first');
    });
});

describe('reconnecting', () => {
    it('is not undone by the old connection reporting its own close late', async () => {
        // The bug this pins: anything that changes the socket's URL tears the
        // old connection down and starts a new one in the same tick, but
        // `disconnect()` does not close the socket synchronously -- the real
        // RFB fires `disconnect` only once the server confirms it, which
        // lands *after* the new connection already exists. The old handler
        // nulled the (shared, by-ref) `rfbRef` unconditionally, so every
        // paste and keystroke after that landed on a null ref while the
        // picture went on rendering the new connection's frames.
        const { container, rerender } = render(
            <BrowserPane origin="https://a.example" />,
        );
        const first = await connect();

        rerender(<BrowserPane origin="https://b.example" />);
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
        const second = rfbInstances[1];
        act(() => second.emit('connect'));

        // The stale instance's close finally reports in, after the new one
        // is already live.
        act(() => first.emit('disconnect'));

        const target = container.querySelector('[role="application"]');
        fireEvent.paste(target!, { clipboardData: { getData: () => 'hunter2' } });
        expect(second.clipboardWrites).toEqual(['hunter2']);
    });
});

describe('a disconnect that will not fix itself by retrying', () => {
    // The regression this pins: RFB's own `disconnect` event carries only
    // `{clean: boolean}`, which cannot distinguish "the browser is not
    // running" (retrying is right -- the agent may start one) from "your
    // session expired" (retrying with the same token never succeeds). The
    // first version of this file's VNC swap lost that distinction entirely
    // and retried every disconnect identically, which is what an expired
    // sign-in looked like in practice: "The connection dropped. Reconnecting."
    // forever, on a loop that could never end.
    it('shows "not signed in" and stops, on an unauthenticated close', () => {
        return renderAndClose(4401, 'You are not signed in');
    });

    it('shows "needs restarting" and stops, on a stale-image close', () => {
        return renderAndClose(4426, 'This computer needs restarting');
    });

    it('shows "not available" and stops, on an unsupported-fabric close', () => {
        return renderAndClose(4422, 'Not available on this computer');
    });

    it('keeps retrying on "no browser running", unlike the terminal codes', async () => {
        render(<BrowserPane origin="https://example.com" />);
        const first = await connect();
        act(() => {
            first.socket!.closeWith(4409);
            first.emit('disconnect');
        });

        await waitFor(() => expect(rfbInstances).toHaveLength(2));
    });

    it('keeps the picture through a drop it expects to recover from', async () => {
        // Blanking to "Connecting..." on every hiccup is what made a live
        // pane feel broken: the page was still there a second later, but the
        // screen said the browser had gone. Once a frame has arrived it stays
        // until something terminal replaces it.
        render(<BrowserPane origin="https://example.com" />);
        const first = await connect();
        act(() => {
            first.socket!.closeWith(1006);
            first.emit('disconnect');
        });

        expect(screen.queryByText('The connection dropped')).toBeNull();
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
    });

    it('does explain itself when it never had a picture to keep', async () => {
        render(<BrowserPane origin="https://example.com" />);
        // No `connect` event: nothing has ever painted here.
        await waitFor(() => expect(rfbInstances).toHaveLength(1));
        act(() => {
            rfbInstances[0].socket!.closeWith(1006);
            rfbInstances[0].emit('disconnect');
        });

        expect(screen.getByText('The connection dropped')).toBeTruthy();
    });

    async function renderAndClose(code: number, title: string): Promise<void> {
        render(<BrowserPane origin="https://example.com" />);
        const rfb = await connect();
        act(() => {
            rfb.socket!.closeWith(code);
            rfb.emit('disconnect');
        });

        expect(screen.getByText(title)).toBeTruthy();
        // Given time to prove it, rather than merely not yet having retried.
        await new Promise((resolve) => setTimeout(resolve, 50));
        expect(rfbInstances).toHaveLength(1);
    }
});

describe('paste', () => {
    const XK_CONTROL_L = 0xffe3;
    const XK_LOWER_V = 0x76;

    it('writes the clipboard and then sends a real Ctrl+V, in that order', async () => {
        const { container } = render(
            <BrowserPane origin="https://example.com" />,
        );
        const rfb = await connect();
        const target = container.querySelector('[role="application"]');
        expect(target).toBeTruthy();

        fireEvent.paste(target!, { clipboardData: { getData: () => 'hunter2' } });

        expect(rfb.clipboardWrites).toEqual(['hunter2']);
        // Order matters: sending the keystroke first would let RFB's own
        // keyboard capture race the clipboard write and paste stale content
        // -- see the comment on `sendCtrlV` in `browser-pane.tsx`.
        expect(rfb.sentKeys[0]).toEqual([XK_CONTROL_L, 'ControlLeft', true]);
        expect(rfb.sentKeys[1]).toEqual([XK_LOWER_V, 'KeyV', true]);
        expect(rfb.sentKeys[2]).toEqual([XK_LOWER_V, 'KeyV', false]);
        expect(rfb.sentKeys[3]).toEqual([XK_CONTROL_L, 'ControlLeft', false]);
    });

    it('works on a pane that is watching a run, not only on a sign-in', async () => {
        // This used to assert the opposite, because a watch pane was
        // read-only. Pasting into the agent's browser is now as legitimate as
        // clicking in it, and neither holds the agent up.
        const { container } = render(<BrowserPane conversationId="conv-1" />);
        const rfb = await connect();
        const target = container.querySelector('[role="application"]');
        expect(target).toBeTruthy();

        fireEvent.paste(target!, { clipboardData: { getData: () => 'hunter2' } });

        expect(rfb.clipboardWrites).toEqual(['hunter2']);
    });
});

describe('a sign-in answered late', () => {
    it('says it is still opening the site, rather than showing a blank browser', async () => {
        // The case somebody hits after stepping away: the pause is hours old,
        // the browser it was aimed at has been retired, and clicking "Open
        // asur.work" reconnects to a display showing about:blank. The pane
        // used to paint that and stop -- indistinguishable from "done".
        page.url = 'about:blank';
        render(<BrowserPane origin="https://asur.work" />);
        await connect();

        expect(await screen.findByText('Opening asur.work…')).toBeTruthy();
    });

    it('clears once the browser reports it got there', async () => {
        page.url = 'https://asur.work/auth';
        render(<BrowserPane origin="https://asur.work" />);
        await connect();

        await waitFor(() => expect(rfbInstances[0].url).toContain('origin='));
        await waitFor(() =>
            expect(screen.queryByText('Opening asur.work…')).toBeNull(),
        );
    });

    it('offers a way to re-steer a browser that never arrived', async () => {
        // Reconnecting is the re-steer: `ensure_browser` points the browser at
        // the origin again on every connect. Without this the only recovery
        // was reloading the page, because clicking "Open" a second time
        // resolves the same origin and changes nothing the pane watches.
        page.url = 'about:blank';
        render(<BrowserPane origin="https://asur.work" />);
        await connect();

        await screen.findByText('Opening asur.work…');
        expect(rfbInstances).toHaveLength(1);

        screen.getByRole('button', { name: 'Try again' }).click();

        await waitFor(() => expect(rfbInstances.length).toBe(2));
    });
});

describe('the clipboard, both ways', () => {
    const CTRL_L = 0xffe3;

    it('sends Ctrl+C when a Mac presses Cmd+C', async () => {
        // Passed through, Cmd arrives at a Linux browser as Super+c and
        // copies nothing -- the gesture silently does nothing at all.
        const { container } = render(<BrowserPane origin="https://asur.work" />);
        const rfb = await connect();
        const target = container.querySelector('[role="application"]')!;

        fireEvent.keyDown(target, { key: 'c', metaKey: true });

        expect(rfb.sentKeys).toEqual([
            [CTRL_L, 'ControlLeft', true],
            ['c'.charCodeAt(0), 'KeyC', true],
            ['c'.charCodeAt(0), 'KeyC', false],
            [CTRL_L, 'ControlLeft', false],
        ]);
    });

    it('leaves Cmd+V to the paste event, so nothing is pasted twice', async () => {
        // The paste event writes the remote clipboard first and then types,
        // which is what makes it race-free. Handling the keystroke as well
        // would fire a second, empty paste.
        const { container } = render(<BrowserPane origin="https://asur.work" />);
        const rfb = await connect();
        const target = container.querySelector('[role="application"]')!;

        fireEvent.keyDown(target, { key: 'v', metaKey: true });

        expect(rfb.sentKeys).toEqual([]);
    });

    it('puts what the remote copied onto this machine', async () => {
        const written: string[] = [];
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: async (text: string) => void written.push(text) },
        });
        render(<BrowserPane origin="https://asur.work" />);
        const rfb = await connect();

        act(() => rfb.emit('clipboard', { detail: { text: 'copied over there' } }));

        expect(written).toEqual(['copied over there']);
    });
});

describe('keeping the display the shape of the pane', () => {
    /**
     * jsdom gives every element a zero-size box, so `getBoundingClientRect`
     * reports 0x0 and the pane's own guard skips it. Measuring is the
     * browser's job and not what these assert; what they assert is *when* a
     * request goes out.
     */
    const measure = (width: number, height: number) => {
        Element.prototype.getBoundingClientRect = function () {
            return { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
        };
    };
    const realRect = Element.prototype.getBoundingClientRect;
    afterEach(() => {
        Element.prototype.getBoundingClientRect = realRect;
    });

    it('asks once the picture is live, not merely once it is mounted', async () => {
        measure(900, 700);
        render(<BrowserPane origin="https://a.example" />);

        // Mounting is not connecting. Asking before there is a display to
        // resize is a request into a sandbox that may not be running.
        expect(resized).toEqual([]);

        await connect();
        await waitFor(() => expect(resized).toEqual(['900x700']));
    });

    it('re-asserts the size on a reconnect, which is the bug it exists for', async () => {
        // The pane memoised the last size it had asked for and depended on
        // nothing, so the guard outlived the connection it was true of. After
        // a sandbox resume or an Xvfb restart the display comes back at its
        // starting size; the pane's own box never changed, so no observer
        // fired, and the memo said "already asked for that". The picture
        // stayed letterboxed with no way back short of dragging the window.
        measure(900, 700);
        render(<BrowserPane origin="https://a.example" />);
        const first = await connect();
        await waitFor(() => expect(resized).toEqual(['900x700']));

        act(() => first.socket!.closeWith(1006));
        act(() => first.emit('disconnect'));

        await waitFor(() => expect(rfbInstances.length).toBeGreaterThan(1));
        act(() => rfbInstances[rfbInstances.length - 1].emit('connect'));

        await waitFor(() => expect(resized).toEqual(['900x700', '900x700']));
    });

    it('does not repeat itself while one connection stays up', async () => {
        measure(900, 700);
        render(<BrowserPane origin="https://a.example" />);
        await connect();
        await waitFor(() => expect(resized).toEqual(['900x700']));

        // The observer firing with the same box is the common case: a
        // re-layout that did not change anything. One display resize is an X
        // mode change behind a sandbox round trip, so it must not be sent
        // again for a size already asked for.
        act(() => {
            observers[observers.length - 1].callback(
                [{ contentRect: { width: 900, height: 700 } } as ResizeObserverEntry],
                {} as ResizeObserver,
            );
        });
        await new Promise((resolve) => setTimeout(resolve, 300));

        expect(resized).toEqual(['900x700']);
    });
});
