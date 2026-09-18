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
 * display to match its own size. Never fired here: what a resize *does* is a
 * round trip to the backend, which belongs to the tests for that endpoint
 * rather than to a component test standing in front of a fake socket. This
 * exists so constructing one does not throw.
 */
class FakeResizeObserver {
    // Same signature as the real constructor, and it keeps what it is handed.
    // A stub that took no callback would still work here -- nothing fires it --
    // but it would be a narrower contract than the thing it replaces, which is
    // how a test comes to pass against a call the browser would reject.
    readonly callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
    }
    observe() {}
    unobserve() {}
    disconnect() {}
}
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
    private listeners: Record<string, Array<() => void>> = {};

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

    addEventListener(type: string, listener: () => void) {
        (this.listeners[type] ??= []).push(listener);
    }

    emit(type: string) {
        for (const listener of this.listeners[type] ?? []) listener();
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

afterEach(() => {
    rfbInstances.length = 0;
    cleanup();
});

const connect = async () => {
    await waitFor(() => expect(rfbInstances).toHaveLength(1));
    const rfb = rfbInstances[0];
    act(() => rfb.emit('connect'));
    return rfb;
};

describe('opening the view', () => {
    it('asks for view mode by default, and control mode with autoControl', async () => {
        render(<BrowserPane origin="https://example.com" />);
        const rfb = await connect();
        expect(rfb.url).toContain('mode=view');
        expect(rfb.viewOnly).toBe(true);

        cleanup();
        rfbInstances.length = 0;
        render(<BrowserPane origin="https://example.com" autoControl />);
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

describe('taking control', () => {
    it('flips viewOnly on the live connection when the toggle is used', async () => {
        const { getByRole } = render(<BrowserPane origin="https://example.com" />);
        await connect();

        fireEvent.click(getByRole('button', { name: 'Take control' }));

        // A new connection: the mode is part of the URL, not a message sent
        // over an existing one, so driving reconnects rather than upgrading.
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
        const driving = rfbInstances[1];
        act(() => driving.emit('connect'));
        expect(driving.viewOnly).toBe(false);
        expect(driving.url).toContain('mode=control');
    });

    it('is not undone by the old connection reporting its own close late', async () => {
        // The bug this pins: toggling `controlling` tears down the view-mode
        // connection and starts a control-mode one in the same tick, but
        // `disconnect()` does not close the socket synchronously -- the real
        // RFB fires `disconnect` only once the server actually confirms it,
        // which lands *after* the new connection already exists. The old
        // handler nulled the (shared, by-ref) `rfbRef` unconditionally, so
        // every paste and keystroke after the first "Take control" landed on
        // a null ref while the picture went on rendering the new
        // connection's frames -- nothing on screen said so.
        const { getByRole, container } = render(<BrowserPane origin="https://example.com" />);
        const watching = await connect();

        fireEvent.click(getByRole('button', { name: 'Take control' }));
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
        const driving = rfbInstances[1];
        act(() => driving.emit('connect'));

        // The stale instance's close finally reports in, after the new one
        // is already live.
        act(() => watching.emit('disconnect'));

        const target = container.querySelector('[role="application"]');
        fireEvent.paste(target!, { clipboardData: { getData: () => 'hunter2' } });
        expect(driving.clipboardWrites).toEqual(['hunter2']);
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

        expect(screen.getByText('The browser is not running')).toBeTruthy();
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
    });

    it('keeps retrying on an ordinary drop, same as before', async () => {
        render(<BrowserPane origin="https://example.com" />);
        const first = await connect();
        act(() => {
            first.socket!.closeWith(1006);
            first.emit('disconnect');
        });

        expect(screen.getByText('The connection dropped')).toBeTruthy();
        await waitFor(() => expect(rfbInstances).toHaveLength(2));
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
            <BrowserPane origin="https://example.com" autoControl />,
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

    it('does nothing while only watching', async () => {
        const { container } = render(<BrowserPane origin="https://example.com" />);
        const rfb = await connect();
        const target = container.querySelector('[role="img"]');

        fireEvent.paste(target!, { clipboardData: { getData: () => 'hunter2' } });

        expect(rfb.clipboardWrites).toEqual([]);
        expect(rfb.sentKeys).toEqual([]);
    });
});
