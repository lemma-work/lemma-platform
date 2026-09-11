/**
 * Watching, and driving, the browser inside your own sandbox.
 *
 * The socket carries four message types each way; it is not the Chrome
 * debugging protocol. That means this file is where a click becomes a pair of
 * coordinates and a keystroke becomes a key event, and those two translations
 * are the whole of it.
 *
 * Both are easy to get subtly wrong in ways nothing fails on. A previous
 * version mapped clicks against the canvas element's box while the image inside
 * it was letterboxed, so every click landed off-target; sent Enter in a form
 * that never submitted; scrolled pages the wrong way; and leaked a socket
 * whenever it was closed while still connecting. There are tests for each of
 * those below this file's own directory.
 */

import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';

export type ViewerState =
    | 'connecting'
    | 'live'
    | 'no-browser'
    | 'unsupported'
    | 'stale-image'
    | 'refused'
    | 'lost';

export interface ViewerFrame {
    /** The page's own pixel size, which is what input coordinates are in. */
    pageWidth: number;
    pageHeight: number;
    bitmap: HTMLImageElement;
}

/** Close codes the API uses. Each maps to a different sentence and remedy. */
const CLOSE_UNAUTHENTICATED = 4401;
const CLOSE_ORIGIN_REFUSED = 4403;
const CLOSE_NO_BROWSER = 4409;
const CLOSE_UNSUPPORTED = 4422;
const CLOSE_STALE_IMAGE = 4426;

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 30_000;

/** Keys that carry no character. Everything else types itself. */
const NON_TEXT_KEYS = new Set([
    'Backspace', 'Tab', 'Enter', 'Shift', 'Control', 'Alt', 'Meta', 'Escape',
    'ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown', 'Home', 'End',
    'PageUp', 'PageDown', 'Delete', 'Insert',
]);

/**
 * Where a click on the canvas lands on the page.
 *
 * The canvas is drawn with `object-fit: contain`, so the image is letterboxed
 * inside the element whenever their aspect ratios differ — and the element's
 * box is therefore not the picture's box. Mapping against the element is the
 * bug that made every click land near, but not on, what was aimed at.
 */
export const toPagePoint = (
    rect: { left: number; top: number; width: number; height: number },
    frame: { pageWidth: number; pageHeight: number },
    event: { clientX: number; clientY: number },
): { x: number; y: number } => {
    if (!rect.width || !rect.height || !frame.pageWidth || !frame.pageHeight) {
        return { x: 0, y: 0 };
    }
    const scale = Math.min(rect.width / frame.pageWidth, rect.height / frame.pageHeight);
    const drawnWidth = frame.pageWidth * scale;
    const drawnHeight = frame.pageHeight * scale;
    const offsetX = (rect.width - drawnWidth) / 2;
    const offsetY = (rect.height - drawnHeight) / 2;

    const x = (event.clientX - rect.left - offsetX) / scale;
    const y = (event.clientY - rect.top - offsetY) / scale;
    return {
        x: Math.max(0, Math.min(frame.pageWidth, Math.round(x))),
        y: Math.max(0, Math.min(frame.pageHeight, Math.round(y))),
    };
};

const modifiersOf = (event: {
    altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean;
}): number =>
    (event.altKey ? 1 : 0) | (event.ctrlKey ? 2 : 0) |
    (event.metaKey ? 4 : 0) | (event.shiftKey ? 8 : 0);

/**
 * A keyboard event as the browser in the sandbox needs to receive it.
 *
 * `text` is what makes a character appear *and* what makes a form submit:
 * Blink performs implicit submission from the keypress handler, and a key sent
 * without text generates no keypress. Sending Enter as a bare `rawKeyDown` is
 * why the first version could not sign anybody in — the password was typed and
 * then nothing happened.
 */
export const keyEventFor = (event: {
    key: string; code: string; keyCode: number; type: string;
    altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean;
}): Record<string, unknown> => {
    const isUp = event.type === 'keyup';
    const isShortcut = event.ctrlKey || event.metaKey;
    const printable = event.key.length === 1 && !isShortcut;

    let text: string | undefined;
    if (!isUp && printable) text = event.key;
    if (!isUp && event.key === 'Enter') text = '\r';

    return {
        kind: 'key',
        type: isUp ? 'keyUp' : text !== undefined ? 'keyDown' : 'rawKeyDown',
        key: event.key,
        code: event.code,
        ...(text !== undefined ? { text } : {}),
        // Set for every key, not just the named ones: a shortcut like ctrl+A
        // is inert without it, which is how "select all" silently did nothing.
        windowsVirtualKeyCode: event.keyCode,
        nativeVirtualKeyCode: event.keyCode,
        modifiers: modifiersOf(event),
    };
};

/**
 * A wheel event, in the sign convention Chrome expects.
 *
 * The same one the DOM uses: positive `deltaY` scrolls the page down. Negating
 * it — which the first version did — scrolls every page backwards.
 */
export const wheelEventFor = (
    point: { x: number; y: number },
    event: { deltaX: number; deltaY: number; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean },
): Record<string, unknown> => ({
    kind: 'wheel',
    type: 'mouseWheel',
    x: point.x,
    y: point.y,
    deltaX: event.deltaX,
    deltaY: event.deltaY,
    modifiers: modifiersOf(event),
});

export const closeCodeToState = (code: number): ViewerState => {
    switch (code) {
        case CLOSE_UNAUTHENTICATED:
        case CLOSE_ORIGIN_REFUSED:
            return 'refused';
        case CLOSE_NO_BROWSER:
            return 'no-browser';
        case CLOSE_UNSUPPORTED:
            return 'unsupported';
        case CLOSE_STALE_IMAGE:
            return 'stale-image';
        default:
            return 'lost';
    }
};

export const reconnectDelayMs = (attempt: number): number =>
    Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt) * Math.random();

export interface ViewerOptions {
    mode: 'view' | 'control';
    origin?: string;
    accessToken?: string;
    onFrame: (frame: ViewerFrame) => void;
    onState: (state: ViewerState) => void;
    onNavigated?: (url: string) => void;
}

export const viewSocketUrl = (options: {
    mode: string; origin?: string; accessToken?: string;
}): string => {
    const base = getLemmaApiBaseUrl().replace(/^http/, 'ws').replace(/\/$/, '');
    const query = new URLSearchParams({ mode: options.mode });
    if (options.origin) query.set('origin', options.origin);
    // In the URL because a browser cannot set headers on a WebSocket handshake
    // — the same reason the datastore changes socket does it.
    if (options.accessToken) query.set('access_token', options.accessToken);
    return `${base}/workspace/browser/view?${query.toString()}`;
};

export interface ViewerHandle {
    send: (message: Record<string, unknown>) => void;
    close: () => void;
}

/** Open the view, and keep it open across drops. */
export function openBrowserView(options: ViewerOptions): ViewerHandle {
    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let latest: { pageWidth: number; pageHeight: number } | null = null;

    const send = (message: Record<string, unknown>) => {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(message));
        }
    };

    const connect = () => {
        if (closed) return;
        options.onState('connecting');
        socket = new WebSocket(
            viewSocketUrl({
                mode: options.mode,
                origin: options.origin,
                accessToken: options.accessToken,
            }),
        );

        socket.onmessage = (event) => {
            let message: Record<string, unknown>;
            try {
                message = JSON.parse(String(event.data));
            } catch {
                return;
            }
            if (message.t === 'frame') {
                const bitmap = new Image();
                bitmap.onload = () => {
                    latest = {
                        pageWidth: Number(message.w) || bitmap.width,
                        pageHeight: Number(message.h) || bitmap.height,
                    };
                    options.onFrame({ ...latest, bitmap });
                    // Acknowledged only once it is painted, so a slow viewer
                    // gets fewer frames rather than a growing backlog.
                    send({ t: 'ack', seq: message.seq });
                };
                bitmap.src = `data:image/jpeg;base64,${String(message.data)}`;
                return;
            }
            if (message.t === 'status' && message.state === 'attached') {
                attempt = 0;
                options.onState('live');
                return;
            }
            if (message.t === 'navigated' && options.onNavigated) {
                options.onNavigated(String(message.url ?? ''));
            }
        };

        socket.onclose = (event) => {
            if (closed) return;
            const state = closeCodeToState(event.code);
            options.onState(state);
            // A refusal will not become an acceptance by asking again, and a
            // sandbox that cannot do this at all never will.
            if (state === 'refused' || state === 'unsupported' || state === 'stale-image') {
                return;
            }
            retryTimer = setTimeout(connect, reconnectDelayMs(attempt++));
        };
    };

    connect();

    return {
        send,
        close: () => {
            closed = true;
            if (retryTimer) clearTimeout(retryTimer);
            // Closed whatever its state. Closing only an OPEN socket leaves a
            // CONNECTING one to open unobserved, start a screencast nobody is
            // watching, and keep the sandbox awake — which is what happened
            // every time somebody switched tabs during a cold start.
            if (socket && socket.readyState !== WebSocket.CLOSED) {
                socket.close();
            }
            socket = null;
        },
    };
}

export const currentFrameSize = (frame: ViewerFrame | null) =>
    frame ? { pageWidth: frame.pageWidth, pageHeight: frame.pageHeight } : null;

export { NON_TEXT_KEYS, modifiersOf };
