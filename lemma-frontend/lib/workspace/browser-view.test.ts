/**
 * The translations between a person's input and the browser in the sandbox.
 *
 * Each of these covers a bug the previous version shipped, all four of which
 * were invisible: the picture looked right, the socket stayed open, and the
 * clicks simply went somewhere else.
 *
 * In `lib/` rather than beside a component on purpose — the vitest include list
 * is literal, and a test placed under `components/` would never run.
 */

import { describe, expect, it } from 'vitest';

import {
    closeCodeToState,
    keyEventFor,
    reconnectDelayMs,
    toFramePoint,
    wheelEventFor,
} from './browser-view';

const key = (over: Partial<Parameters<typeof keyEventFor>[0]> = {}) => ({
    key: 'a',
    code: 'KeyA',
    keyCode: 65,
    type: 'keydown',
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    ...over,
});

describe('mapping a click onto the page', () => {
    const frame = { pictureWidth: 1280, pictureHeight: 800 };

    it('accounts for the letterbox when the pane is a different shape', () => {
        // 800x800 pane, 1280x800 page: the image is drawn 800x500 with 150px
        // bars above and below. Mapping against the element's box instead put
        // every click 150px out.
        const rect = { left: 0, top: 0, width: 800, height: 800 };
        const topOfImage = toFramePoint(rect, frame, { clientX: 0, clientY: 150 });
        expect(topOfImage.y).toBe(0);

        const bottomOfImage = toFramePoint(rect, frame, { clientX: 0, clientY: 650 });
        expect(bottomOfImage.y).toBe(800);
    });

    it('maps the centre to the centre', () => {
        const rect = { left: 0, top: 0, width: 800, height: 800 };
        const point = toFramePoint(rect, frame, { clientX: 400, clientY: 400 });
        expect(point).toEqual({ x: 640, y: 400 });
    });

    it('is exact when the shapes already agree', () => {
        const rect = { left: 0, top: 0, width: 640, height: 400 };
        expect(toFramePoint(rect, frame, { clientX: 320, clientY: 200 })).toEqual({
            x: 640,
            y: 400,
        });
    });

    it('accounts for where the pane sits on the screen', () => {
        const rect = { left: 100, top: 50, width: 640, height: 400 };
        expect(toFramePoint(rect, frame, { clientX: 100, clientY: 50 })).toEqual({
            x: 0,
            y: 0,
        });
    });

    it('never reports a point outside the page', () => {
        const rect = { left: 0, top: 0, width: 640, height: 400 };
        const point = toFramePoint(rect, frame, { clientX: 9999, clientY: 9999 });
        expect(point.x).toBeLessThanOrEqual(frame.pictureWidth);
        expect(point.y).toBeLessThanOrEqual(frame.pictureHeight);
    });

    it('survives being asked before the first frame', () => {
        const rect = { left: 0, top: 0, width: 0, height: 0 };
        expect(toFramePoint(rect, frame, { clientX: 10, clientY: 10 })).toEqual({
            x: 0,
            y: 0,
        });
    });

    it('stays in the picture, which is not the size of the page', () => {
        // Measured: a 1280x720 device arrives as a 985x800 JPEG, because the
        // stream encodes within the caps the image sets. The stream server
        // scales input back out of the *frame's* space, so this must answer in
        // the picture's pixels -- answering in the page's put the pointer past
        // the right edge of the picture, where it clicked nothing at all, which
        // is indistinguishable from input never arriving.
        const shrunk = { pictureWidth: 985, pictureHeight: 800 };
        const rect = { left: 0, top: 0, width: 985, height: 800 };
        expect(toFramePoint(rect, shrunk, { clientX: 955, clientY: 400 })).toEqual({
            x: 955,
            y: 400,
        });
        expect(
            toFramePoint(rect, shrunk, { clientX: 9999, clientY: 9999 }).x,
        ).toBe(985);
    });
});

describe('keyboard', () => {
    it('sends Enter with carriage-return text so a form submits', () => {
        // Blink submits from the keypress handler, and a key with no text
        // generates no keypress. Without this the password is typed and
        // pressing Enter does nothing at all — which is every login form.
        const event = keyEventFor(key({ key: 'Enter', code: 'Enter', keyCode: 13 }));
        expect(event.type).toBe('input_keyboard');
        expect(event.eventType).toBe('keyDown');
        expect(event.text).toBe('\r');
    });

    it('types a printable character', () => {
        const event = keyEventFor(key());
        expect(event.eventType).toBe('keyDown');
        expect(event.text).toBe('a');
    });

    it('sends a shortcut without text, and with a key code', () => {
        // `windowsVirtualKeyCode` is what makes ctrl+A mean select-all rather
        // than nothing; the first version set it only for named keys.
        const event = keyEventFor(key({ ctrlKey: true }));
        expect(event.text).toBeUndefined();
        expect(event.windowsVirtualKeyCode).toBe(65);
        expect(event.modifiers).toBe(2);
    });

    it('sends a named key with no text', () => {
        const event = keyEventFor(key({ key: 'Backspace', code: 'Backspace', keyCode: 8 }));
        expect(event.eventType).toBe('rawKeyDown');
        expect(event.text).toBeUndefined();
    });

    it('never puts text on a key release', () => {
        const event = keyEventFor(key({ type: 'keyup' }));
        expect(event.eventType).toBe('keyUp');
        expect(event.text).toBeUndefined();
    });

    it('combines modifiers', () => {
        const event = keyEventFor(key({ shiftKey: true, altKey: true }));
        expect(event.modifiers).toBe(9);
    });
});

describe('wheel', () => {
    it('keeps the browser sign convention', () => {
        // Positive deltaY scrolls down in the DOM and in Chrome alike.
        // Negating it scrolled every page the wrong way.
        const event = wheelEventFor(
            { x: 10, y: 20 },
            { deltaX: 4, deltaY: 120, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
        );
        expect(event.deltaY).toBe(120);
        expect(event.deltaX).toBe(4);
    });

    it('is an input_mouse message, like every other pointer event', () => {
        // The stream server sorts input by device, not by gesture: a scroll is
        // a mouse event whose `eventType` says so. Sending `{type:
        // 'mouseWheel'}` -- which is what CDP wanted -- is refused by the relay
        // as an unknown message, so the page simply never scrolls.
        const event = wheelEventFor(
            { x: 10, y: 20 },
            { deltaX: 0, deltaY: 120, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
        );
        expect(event.type).toBe('input_mouse');
        expect(event.eventType).toBe('mouseWheel');
    });
});

describe('what a close code means', () => {
    it('separates the ones with different remedies', () => {
        expect(closeCodeToState(4401)).toBe('refused');
        expect(closeCodeToState(4403)).toBe('refused');
        expect(closeCodeToState(4409)).toBe('no-browser');
        expect(closeCodeToState(4422)).toBe('unsupported');
        expect(closeCodeToState(4426)).toBe('stale-image');
        expect(closeCodeToState(1006)).toBe('lost');
    });
});

describe('reconnecting', () => {
    it('backs off, with jitter, to a ceiling', () => {
        for (let attempt = 0; attempt < 12; attempt += 1) {
            const delay = reconnectDelayMs(attempt);
            expect(delay).toBeGreaterThanOrEqual(0);
            expect(delay).toBeLessThanOrEqual(30_000);
        }
    });
});
