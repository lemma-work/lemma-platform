/**
 * Watching, and driving, the browser inside your own sandbox.
 *
 * The wire is `agent-browser`'s own stream protocol, proxied by the relay: JPEG
 * frames out, `input_mouse` / `input_keyboard` / `input_touch` in. We used to
 * drive CDP ourselves and invent a protocol for it; the CLI ships a
 * session-scoped stream server that does the same and more, so this file is now
 * only the translation from a DOM event into one of those messages.
 *
 * That translation is easy to get subtly wrong in ways nothing fails on. A
 * previous version mapped clicks against the canvas element's box while the
 * image inside it was letterboxed, so every click landed off-target; sent Enter
 * in a form that never submitted; scrolled pages the wrong way; and leaked a
 * socket whenever it was closed while still connecting. There are tests for
 * each of those below this file's own directory.
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
    /**
     * The picture's own pixels: what the canvas is sized to and what the
     * hit-testing is done against, because the picture is what is on screen.
     *
     * Not what input is sent in. See `deviceWidth`.
     */
    pictureWidth: number;
    pictureHeight: number;
    /**
     * The page's own pixels, from `metadata.deviceWidth` / `deviceHeight`, and
     * the space every input message must be in.
     *
     * The two differ: the stream encodes within the caps the image sets
     * (`AGENT_BROWSER_STREAM_MAX_WIDTH` / `_MAX_HEIGHT`), so a 1050x797 page
     * arrives as a 949x720 JPEG. An earlier version of this file asserted, at
     * length, that the server scaled input back out of the picture's space
     * itself and that this metadata was therefore unused. That was wrong, and
     * the comment saying so was the most confident thing in the file.
     *
     * Settled by experiment rather than by reading: a 44x22 button at page
     * (800,700) in a real sandbox, clicked through the stream socket twice.
     * Page coordinates (822,711) set the title; picture coordinates (743,642)
     * did nothing. Every click was landing about a tenth of the way up and to
     * the left -- which large targets absorb and small ones do not, so it
     * presented as "clicks sometimes work" rather than as a broken mapping.
     */
    deviceWidth: number;
    deviceHeight: number;
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
 * Where a click on the canvas lands on the picture.
 *
 * The canvas is drawn with `object-fit: contain`, so the picture is letterboxed
 * inside the element whenever their aspect ratios differ — and the element's
 * box is therefore not the picture's box. Mapping against the element is the
 * bug that made every click land near, but not on, what was aimed at.
 *
 * The result is in the picture's pixels, which is what the stream server
 * expects: it knows how it scaled the frame and scales input back the same way.
 * Sending the page's coordinates instead — which the frame's metadata also
 * carries, and which are larger — puts the pointer past the picture's right
 * edge, where it hits nothing. That failed silently: a click that lands on no
 * element looks exactly like input that never arrived.
 */
export const toFramePoint = (
    rect: { left: number; top: number; width: number; height: number },
    frame: {
        pictureWidth: number;
        pictureHeight: number;
        deviceWidth?: number;
        deviceHeight?: number;
    },
    event: { clientX: number; clientY: number },
): { x: number; y: number } => {
    if (!rect.width || !rect.height || !frame.pictureWidth || !frame.pictureHeight) {
        return { x: 0, y: 0 };
    }
    // Two spaces, and the whole of this function is the conversion between
    // them. Hit-testing is against the *picture*, because that is what is drawn
    // and what the person is aiming at. The answer is in *page* pixels, because
    // that is what the stream server dispatches.
    const scale = Math.min(
        rect.width / frame.pictureWidth,
        rect.height / frame.pictureHeight,
    );
    const drawnWidth = frame.pictureWidth * scale;
    const drawnHeight = frame.pictureHeight * scale;
    const offsetX = (rect.width - drawnWidth) / 2;
    const offsetY = (rect.height - drawnHeight) / 2;

    // Where in the drawn picture, as a fraction. Going through a fraction
    // rather than through picture pixels means the picture's size drops out
    // entirely, which is the point: it is a display detail and input never
    // depended on it.
    const across = (event.clientX - rect.left - offsetX) / drawnWidth;
    const down = (event.clientY - rect.top - offsetY) / drawnHeight;

    // Falling back to the picture's size keeps a frame that somehow arrived
    // without metadata roughly usable, rather than sending every click to 0,0.
    const width = frame.deviceWidth || frame.pictureWidth;
    const height = frame.deviceHeight || frame.pictureHeight;
    return {
        x: Math.max(0, Math.min(width, Math.round(across * width))),
        y: Math.max(0, Math.min(height, Math.round(down * height))),
    };
};

/**
 * A run of text as the stream will actually insert it: one message per
 * character.
 *
 * `char` carries a single character, the way CDP's `Input.dispatchKeyEvent`
 * does. Handed a whole string it inserts **nothing at all** — measured: one
 * message of `"LONGSTRING"` left the field empty, seven messages spelling
 * `"PERCHAR"` filled it. So the mobile text bar, whose entire job is to send a
 * string, silently did nothing for any word longer than a letter, and a pasted
 * password did the same.
 *
 * Split by code point rather than by UTF-16 unit, so an emoji or an astral
 * character is one message instead of two halves of a surrogate pair.
 */
export const textAsCharEvents = (text: string): Record<string, unknown>[] =>
    Array.from(text).map((character) => ({
        type: 'input_keyboard',
        eventType: 'char',
        text: character,
    }));

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
        type: 'input_keyboard',
        eventType: isUp ? 'keyUp' : text !== undefined ? 'keyDown' : 'rawKeyDown',
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
    type: 'input_mouse',
    eventType: 'mouseWheel',
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
    conversationId?: string;
    onFrame: (frame: ViewerFrame) => void;
    onState: (state: ViewerState) => void;
    onNavigated?: (url: string) => void;
}

export const viewSocketUrl = (options: {
    mode: string; origin?: string; accessToken?: string; conversationId?: string;
}): string => {
    const base = getLemmaApiBaseUrl().replace(/^http/, 'ws').replace(/\/$/, '');
    const query = new URLSearchParams({ mode: options.mode });
    if (options.origin) query.set('origin', options.origin);
    // Which browser to watch. A conversation has its own, so that two of this
    // person's agents do not share cookies; naming it here is what stops every
    // pane attaching to the sandbox's single default session.
    if (options.conversationId) query.set('conversation', options.conversationId);
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
    let latest: Omit<ViewerFrame, 'bitmap'> | null = null;

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
                conversationId: options.conversationId,
            }),
        );

        socket.onmessage = (event) => {
            let message: Record<string, unknown>;
            try {
                message = JSON.parse(String(event.data));
            } catch {
                return;
            }
            // agent-browser's own stream protocol. We proxy its session-scoped
            // stream rather than driving CDP, so these are its message names.
            if (message.type === 'frame') {
                // Acknowledged on arrival, not once painted. The socket asks
                // for `pacing=ack`, so the server holds the next frame until
                // this is sent — and acking from the decode callback put a
                // JPEG decode *and* a round trip between every pair of frames,
                // which caps the rate at one frame per round trip however much
                // bandwidth there is. On a 150ms mobile path that is six
                // frames a second no matter what, which is the whole of the
                // "laggy" complaint. The frame is already in hand here, so
                // asking for the next one while this one decodes cannot make
                // anything stale: the server sends the newest it has.
                send({ type: 'ack', seq: message.seq });
                const bitmap = new Image();
                bitmap.onload = () => {
                    const metadata = (message.metadata || {}) as Record<
                        string,
                        unknown
                    >;
                    latest = {
                        pictureWidth: bitmap.naturalWidth || bitmap.width,
                        pictureHeight: bitmap.naturalHeight || bitmap.height,
                        deviceWidth: Number(metadata.deviceWidth) || 0,
                        deviceHeight: Number(metadata.deviceHeight) || 0,
                    };
                    options.onFrame({ ...latest, bitmap });
                    // Live when there is a picture, not when there is a socket.
                    // The stream sends its opening frame to every client that
                    // connects, including one joining a page that has been
                    // still for an hour, so this always arrives — and saying
                    // "live" before it would uncover an empty canvas.
                    options.onState('live');
                };
                bitmap.src = `data:image/jpeg;base64,${String(message.data)}`;
                return;
            }
            if (message.type === 'status') {
                // Not a picture, so not yet "live" — but proof the connection
                // works, which is what the backoff counts.
                attempt = 0;
                return;
            }
            if (message.type === 'url' && options.onNavigated) {
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
    frame
        ? { pictureWidth: frame.pictureWidth, pictureHeight: frame.pictureHeight }
        : null;

export { NON_TEXT_KEYS, modifiersOf };
