// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { BrowserPane } from './browser-pane';

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
    viewOnly = false;
    scaleViewport = false;
    background = '';
    sentKeys: Array<[number, string, boolean]> = [];
    clipboardWrites: string[] = [];
    private listeners: Record<string, Array<() => void>> = {};

    constructor(target: Element, url: string) {
        this.target = target;
        this.url = url;
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

    it('names no conversation or session', async () => {
        // VNC shows the sandbox's whole shared display, not a session-scoped
        // tab -- there is nothing left for either of those to select.
        render(<BrowserPane origin="https://example.com" />);
        const rfb = await connect();
        expect(rfb.url).not.toContain('conversation=');
        expect(rfb.url).not.toContain('session=');
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
