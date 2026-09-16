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
    textAsCharEvents,
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

    it('answers in the page\'s pixels, not the picture\'s', () => {
        // The bug this function existed to fix, and then caused. The stream
        // encodes within the image's caps, so the picture is smaller than the
        // page -- measured in a real sandbox, a 1050x797 page arrives as a
        // 949x720 JPEG. Input is dispatched against the *page*, so answering in
        // picture pixels put every click about a tenth of the way up and to the
        // left. Large targets absorb that; a cookie banner's Allow does not,
        // which is why it read as "clicks sometimes work".
        const streamed = {
            pictureWidth: 949,
            pictureHeight: 720,
            deviceWidth: 1050,
            deviceHeight: 797,
        };
        // A pane the same shape as the picture: no letterbox, so the only thing
        // under test is which space the answer is in.
        const rect = { left: 0, top: 0, width: 949, height: 720 };

        expect(toFramePoint(rect, streamed, { clientX: 949, clientY: 720 })).toEqual({
            x: 1050,
            y: 797,
        });
        // The real button from the experiment: page (822,711) hit, picture
        // (743,642) missed.
        const aimed = toFramePoint(rect, streamed, { clientX: 743, clientY: 642 });
        expect(aimed).toEqual({ x: 822, y: 711 });
    });

    it('falls back to the picture when a frame carries no metadata', () => {
        // Better roughly right than every click at 0,0.
        const rect = { left: 0, top: 0, width: 949, height: 720 };
        expect(
            toFramePoint(rect, { pictureWidth: 949, pictureHeight: 720 }, {
                clientX: 949,
                clientY: 720,
            }),
        ).toEqual({ x: 949, y: 720 });
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

describe('text the page receives', () => {
    it('sends one message per character, because `char` carries one', () => {
        // Measured against the real stream server: one `char` message holding
        // "LONGSTRING" left the field empty, seven messages spelling "PERCHAR"
        // filled it. The text bar sent whole strings, so it did nothing at all
        // for any word longer than a letter -- and it is the only way to type
        // on a phone, where a canvas gets no key events.
        const events = textAsCharEvents('hi!');
        expect(events).toEqual([
            { type: 'input_keyboard', eventType: 'char', text: 'h' },
            { type: 'input_keyboard', eventType: 'char', text: 'i' },
            { type: 'input_keyboard', eventType: 'char', text: '!' },
        ]);
    });

    it('keeps an astral character whole', () => {
        // Split by UTF-16 unit this would be two halves of a surrogate pair,
        // and each half on its own is not a character anything can insert.
        expect(textAsCharEvents('a😀').map((e) => e.text)).toEqual(['a', '😀']);
    });

    it('sends nothing for an empty string', () => {
        expect(textAsCharEvents('')).toEqual([]);
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
