'use client';

/**
 * Driving the agent's browser from a canvas.
 *
 * Chrome streams JPEG frames over its debugging protocol and takes mouse and
 * key events back the same way. That is the whole viewer: paint what arrives,
 * translate what the person does into the events Chrome understands.
 *
 * Framework-free on purpose — it is a socket, a canvas and a coordinate
 * transform, and none of that wants to re-render.
 */

export interface ViewerFrame {
    /** Base64 JPEG. */
    data: string;
    /** Chrome's own device-independent page size, for mapping clicks back. */
    pageWidth: number;
    pageHeight: number;
}

type Send = (method: string, params?: Record<string, unknown>) => void;

/** Chrome wants a bitmask, and the browser hands us discrete buttons. */
const BUTTON_MASK: Record<number, number> = { 0: 1, 1: 4, 2: 2 };
const BUTTON_NAME: Record<number, string> = { 0: 'left', 1: 'middle', 2: 'right' };

/** Keys Chrome needs told about as keys rather than as typed text. */
const NON_TEXT_KEYS = new Set([
    'Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown',
    'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown',
]);

const modifiersOf = (event: KeyboardEvent | MouseEvent): number =>
    (event.altKey ? 1 : 0) | (event.ctrlKey ? 2 : 0) | (event.metaKey ? 4 : 0) | (event.shiftKey ? 8 : 0);

/**
 * Where a click on the canvas lands on the page.
 *
 * The canvas is whatever size the pane happens to be and the page is whatever
 * size Chrome says — so without this every click lands somewhere near, but not
 * on, the thing that was aimed at, which is maddening in a login form.
 */
const toPagePoint = (
    canvas: HTMLCanvasElement,
    frame: ViewerFrame | null,
    event: MouseEvent,
): { x: number; y: number } => {
    const rect = canvas.getBoundingClientRect();
    if (!frame || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
    return {
        x: ((event.clientX - rect.left) / rect.width) * frame.pageWidth,
        y: ((event.clientY - rect.top) / rect.height) * frame.pageHeight,
    };
};

export type ViewerState =
    | 'connecting'
    | 'live'
    | 'no-browser'
    | 'refused'
    | 'stale-workspace'
    | 'lost';

export interface ViewerHandle {
    close(): void;
}

/**
 * Attach a canvas to a workspace browser stream.
 *
 * `onState` reports what a person should be told: connecting, live, or why not.
 */
export function attachBrowserViewer(options: {
    canvas: HTMLCanvasElement;
    url: string;
    onState: (state: ViewerState) => void;
}): ViewerHandle {
    const { canvas, url, onState } = options;
    const socket = new WebSocket(url);
    const context = canvas.getContext('2d');
    let latest: ViewerFrame | null = null;
    let nextId = 0;
    let closed = false;

    const send: Send = (method, params = {}) => {
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ id: ++nextId, method, params }));
        }
    };

    const paint = (frame: ViewerFrame) => {
        const image = new Image();
        image.onload = () => {
            if (closed || !context) return;
            canvas.width = image.width;
            canvas.height = image.height;
            context.drawImage(image, 0, 0);
        };
        image.src = `data:image/jpeg;base64,${frame.data}`;
    };

    socket.addEventListener('open', () => {
        onState('connecting');
        send('Page.enable');
        // Capped rather than full size: this is a login form on somebody's
        // screen, not a video, and every extra pixel is bandwidth per frame.
        send('Page.startScreencast', {
            format: 'jpeg',
            quality: 60,
            maxWidth: 1280,
            maxHeight: 800,
        });
    });

    socket.addEventListener('message', (event) => {
        let message: { method?: string; params?: Record<string, unknown> };
        try {
            message = JSON.parse(String(event.data));
        } catch {
            return;
        }
        if (message.method !== 'Page.screencastFrame') return;

        const params = message.params ?? {};
        const metadata = (params.metadata ?? {}) as { pageScaleFactor?: number; deviceWidth?: number; deviceHeight?: number };
        latest = {
            data: String(params.data ?? ''),
            pageWidth: Number(metadata.deviceWidth) || canvas.width || 1280,
            pageHeight: Number(metadata.deviceHeight) || canvas.height || 800,
        };
        paint(latest);
        onState('live');
        // Chrome stops sending until each frame is acknowledged, so a missed
        // ack is a picture that freezes rather than one that stutters.
        send('Page.screencastFrameAck', { sessionId: params.sessionId });
    });

    socket.addEventListener('close', (event) => {
        if (closed) return;
        // Codes the bridge uses to say *why*, so the reader gets more than
        // "disconnected".
        if (event.code === 4401) onState('refused');
        else if (event.code === 4409) onState('no-browser');
        // The workspace predates the relay, so reconnecting will never help.
        else if (event.code === 4426) onState('stale-workspace');
        else onState('lost');
    });

    const onMouse = (type: 'mousePressed' | 'mouseReleased' | 'mouseMoved') => (event: MouseEvent) => {
        const point = toPagePoint(canvas, latest, event);
        send('Input.dispatchMouseEvent', {
            type,
            x: point.x,
            y: point.y,
            button: BUTTON_NAME[event.button] ?? 'left',
            buttons: type === 'mouseReleased' ? 0 : (BUTTON_MASK[event.button] ?? 1),
            clickCount: type === 'mouseMoved' ? 0 : 1,
            modifiers: modifiersOf(event),
        });
    };

    const onWheel = (event: WheelEvent) => {
        event.preventDefault();
        const point = toPagePoint(canvas, latest, event);
        send('Input.dispatchMouseEvent', {
            type: 'mouseWheel',
            x: point.x,
            y: point.y,
            deltaX: -event.deltaX,
            deltaY: -event.deltaY,
            modifiers: modifiersOf(event),
        });
    };

    const onKeyDown = (event: KeyboardEvent) => {
        event.preventDefault();
        const isText = event.key.length === 1 && !event.ctrlKey && !event.metaKey;
        send('Input.dispatchKeyEvent', {
            type: isText ? 'keyDown' : 'rawKeyDown',
            key: event.key,
            code: event.code,
            // `text` is what makes a character appear; without it Chrome
            // registers the keypress and types nothing.
            ...(isText ? { text: event.key } : {}),
            windowsVirtualKeyCode: NON_TEXT_KEYS.has(event.key) ? event.keyCode : undefined,
            modifiers: modifiersOf(event),
        });
    };

    const onKeyUp = (event: KeyboardEvent) => {
        event.preventDefault();
        send('Input.dispatchKeyEvent', {
            type: 'keyUp',
            key: event.key,
            code: event.code,
            modifiers: modifiersOf(event),
        });
    };

    const mouseDown = onMouse('mousePressed');
    const mouseUp = onMouse('mouseReleased');
    const mouseMove = onMouse('mouseMoved');

    canvas.addEventListener('mousedown', mouseDown);
    canvas.addEventListener('mouseup', mouseUp);
    canvas.addEventListener('mousemove', mouseMove);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('keydown', onKeyDown);
    canvas.addEventListener('keyup', onKeyUp);
    canvas.addEventListener('contextmenu', (event) => event.preventDefault());

    return {
        close() {
            closed = true;
            canvas.removeEventListener('mousedown', mouseDown);
            canvas.removeEventListener('mouseup', mouseUp);
            canvas.removeEventListener('mousemove', mouseMove);
            canvas.removeEventListener('wheel', onWheel);
            canvas.removeEventListener('keydown', onKeyDown);
            canvas.removeEventListener('keyup', onKeyUp);
            if (socket.readyState === WebSocket.OPEN) {
                send('Page.stopScreencast');
                socket.close();
            }
        },
    };
}
