'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { Input } from '@/components/ui/input';
import { Monitor } from '@/components/ui/icons';
import {
    type ViewerFrame,
    type ViewerHandle,
    type ViewerState,
    keyEventFor,
    openBrowserView,
    textAsCharEvents,
    toFramePoint,
    wheelEventFor,
} from '@/lib/workspace/browser-view';
import { cn } from '@/lib/utils';

/**
 * The agent's browser, live.
 *
 * Watching by default. Driving is a deliberate act — the toggle exists so that
 * a person reading a page cannot type into it by accident, and so that the
 * thing they are about to do is named before they do it.
 */
export function BrowserPane({
    origin,
    accessToken,
    conversationId,
    autoControl = false,
    onNavigated,
}: {
    origin?: string;
    accessToken?: string;
    /** Whose browser this is. A conversation has its own, separate from every
     *  other conversation this person runs; a sign-in page names an `origin`
     *  instead and gets the session belonging to that site. */
    conversationId?: string;
    autoControl?: boolean;
    onNavigated?: (url: string) => void;
}) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const viewerRef = useRef<ViewerHandle | null>(null);
    const frameRef = useRef<ViewerFrame | null>(null);
    const [state, setState] = useState<ViewerState>('connecting');
    const [controlling, setControlling] = useState(autoControl);
    // Whether keystrokes are actually going to the page. This is the canvas's
    // DOM focus, and it is the whole difference between driving and appearing
    // to drive: a person who takes control and types without clicking the
    // picture first sends every keystroke to whatever had focus -- on the
    // conversation screen, the message box. Nothing said so, so the browser
    // looked broken while it was working.
    const [keyboardIsHere, setKeyboardIsHere] = useState(false);

    const paint = useCallback((frame: ViewerFrame) => {
        frameRef.current = frame;
        const canvas = canvasRef.current;
        if (!canvas) return;
        // Sized to the *picture*, not to the page it is of. The stream encodes
        // within the image's caps, so the JPEG is routinely smaller than the
        // viewport -- and a canvas sized to the viewport left the picture in
        // one corner of it with the rest transparent, which `object-contain`
        // then letterboxed as though the empty part were part of the shot.
        canvas.width = frame.pictureWidth;
        canvas.height = frame.pictureHeight;
        canvas.getContext('2d')?.drawImage(frame.bitmap, 0, 0);
    }, []);

    // Taking control points the keyboard at the page, without waiting for a
    // click on it. Deliberate rather than incidental: "Take control" is a
    // person saying they want to type here, and making them click the picture
    // first to be heard is a rule nobody can see.
    useEffect(() => {
        if (!controlling) return;
        const frame = requestAnimationFrame(() => canvasRef.current?.focus());
        return () => cancelAnimationFrame(frame);
    }, [controlling]);

    useEffect(() => {
        const viewer = openBrowserView({
            mode: controlling ? 'control' : 'view',
            origin,
            accessToken,
            conversationId,
            onFrame: paint,
            onState: setState,
            onNavigated,
        });
        viewerRef.current = viewer;
        return () => {
            viewer.close();
            viewerRef.current = null;
        };
    }, [controlling, origin, accessToken, conversationId, paint, onNavigated]);

    // Sent as-is: these already are `agent-browser` stream messages, and the
    // relay forwards them rather than translating. It refuses input outright
    // when the socket is in view mode, so "watching" is not enforced here.
    const sendInput = useCallback((event: Record<string, unknown>) => {
        viewerRef.current?.send(event);
    }, []);

    const pointFor = useCallback((event: { clientX: number; clientY: number }) => {
        const canvas = canvasRef.current;
        const frame = frameRef.current;
        if (!canvas || !frame) return { x: 0, y: 0 };
        return toFramePoint(canvas.getBoundingClientRect(), frame, event);
    }, []);

    const onMouse = useCallback(
        (type: 'mousePressed' | 'mouseReleased' | 'mouseMoved') =>
            (event: React.MouseEvent<HTMLCanvasElement>) => {
                if (!controlling) return;
                const point = pointFor(event);
                sendInput({
                    type: 'input_mouse',
                    eventType: type,
                    x: point.x,
                    y: point.y,
                    button: ['left', 'middle', 'right'][event.button] ?? 'left',
                    // A hover is not a drag. Reporting a held button on every
                    // move made pages with sliders and canvases think one was.
                    buttons: type === 'mouseMoved' ? 0 : 1,
                    clickCount: type === 'mouseMoved' ? 0 : event.detail || 1,
                });
            },
        [controlling, pointFor, sendInput],
    );

    const onWheel = useCallback(
        (event: React.WheelEvent<HTMLCanvasElement>) => {
            if (!controlling) return;
            sendInput(wheelEventFor(pointFor(event), event));
        },
        [controlling, pointFor, sendInput],
    );

    const onKey = useCallback(
        (event: React.KeyboardEvent<HTMLCanvasElement>) => {
            if (!controlling) return;
            // Escape is the way out of the canvas for somebody using a
            // keyboard; swallowing it would trap them here.
            if (event.key === 'Escape') return;
            event.preventDefault();
            sendInput(keyEventFor(event as unknown as Parameters<typeof keyEventFor>[0]));
        },
        [controlling, sendInput],
    );

    /** Text the page receives, a character at a time.
     *
     * Used by the paste handler and by the text bar, whose own reason is that a
     * phone's keyboard reports no usable key events to a canvas.
     */
    const typeText = useCallback(
        (text: string) => {
            for (const message of textAsCharEvents(text)) sendInput(message);
        },
        [sendInput],
    );

    /** What the person pasted, as text the page receives.
     *
     * The sandbox has its own clipboard and it is empty, so forwarding ctrl-V
     * as a key combination pastes nothing. This is the sign-in journey's most
     * common single action -- almost nobody types a password by hand any more,
     * they paste it out of a manager -- so it cannot be the one thing that does
     * not work.
     */
    const onPaste = useCallback(
        (event: React.ClipboardEvent<HTMLCanvasElement>) => {
            if (!controlling) return;
            const text = event.clipboardData.getData('text');
            if (!text) return;
            event.preventDefault();
            typeText(text);
        },
        [controlling, typeText],
    );

    return (
        <div className="flex h-full min-h-0 flex-col gap-2">
            <div className="flex items-center justify-between gap-2 px-1">
                <span className="text-xs text-[var(--text-tertiary)]">
                    {/* Three states, not two. "You are driving" while the
                        keyboard is somewhere else is the sentence that made
                        this feel broken: it says the typing will arrive, and
                        it does not. */}
                    {!controlling
                        ? 'Watching the agent’s browser.'
                        : keyboardIsHere
                          ? 'You are driving. What you type goes to the site.'
                          : 'Click the page to type into it.'}
                </span>
                <Button
                    variant="quiet"
                    size="xs"
                    onClick={() => setControlling((on) => !on)}
                    disabled={state !== 'live'}
                >
                    {controlling ? 'Stop driving' : 'Take control'}
                </Button>
            </div>

            <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-canvas)]">
                <canvas
                    ref={canvasRef}
                    tabIndex={controlling ? 0 : -1}
                    role={controlling ? 'application' : 'img'}
                    aria-label={
                        controlling
                            ? 'The agent’s browser. Click and type to drive it. Press Escape to leave.'
                            : 'The agent’s browser, live'
                    }
                    className={cn(
                        'h-full w-full object-contain outline-none',
                        controlling && 'cursor-crosshair',
                        // A visible edge while the keyboard is pointed here.
                        // `outline-none` above is deliberate -- the browser's
                        // own focus ring is drawn around the *element*, which
                        // includes the letterbox bars, so it sits away from the
                        // picture on most pane shapes and reads as a bug.
                        controlling &&
                            keyboardIsHere &&
                            'ring-2 ring-[var(--action-primary)] ring-inset',
                    )}
                    onMouseDown={(event) => {
                        canvasRef.current?.focus();
                        onMouse('mousePressed')(event);
                    }}
                    onMouseUp={onMouse('mouseReleased')}
                    onMouseMove={onMouse('mouseMoved')}
                    onWheel={onWheel}
                    onKeyDown={onKey}
                    onKeyUp={onKey}
                    onPaste={onPaste}
                    onFocus={() => setKeyboardIsHere(true)}
                    onBlur={() => setKeyboardIsHere(false)}
                />

                {state !== 'live' ? (
                    <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-canvas)] p-6">
                        <EmptyState
                            variant="region"
                            icon={<Monitor />}
                            title={TITLES[state]}
                            description={DESCRIPTIONS[state]}
                        />
                    </div>
                ) : null}
            </div>

            {controlling ? (
                <form
                    className="flex items-center gap-2 px-1"
                    onSubmit={(event) => {
                        event.preventDefault();
                        const input = event.currentTarget.elements.namedItem(
                            'text',
                        ) as HTMLInputElement | null;
                        if (input) {
                            typeText(input.value);
                            input.value = '';
                            // Back to the page. Sending from the bar and then
                            // finding that the next keystroke went nowhere is
                            // the same silent failure in miniature.
                            canvasRef.current?.focus();
                        }
                    }}
                >
                    {/* A phone's keyboard reports no key events to a canvas, so
                        without this there is no way to type on one at all. */}
                    <Input
                        name="text"
                        placeholder="Type here, then press Send"
                        aria-label="Type into the agent’s browser"
                        className="min-w-0 flex-1"
                    />
                    <Button type="submit" variant="secondary" size="xs">
                        Send
                    </Button>
                </form>
            ) : null}
        </div>
    );
}

const TITLES: Record<ViewerState, string> = {
    connecting: 'Connecting…',
    live: '',
    'no-browser': 'The browser is not running',
    unsupported: 'Not available on this computer',
    'stale-image': 'This computer needs restarting',
    refused: 'You are not signed in',
    lost: 'The connection dropped',
};

const DESCRIPTIONS: Record<ViewerState, string> = {
    connecting: 'Waking the computer and starting its browser. The first time takes a moment.',
    live: '',
    'no-browser': 'It starts when the agent opens a page, or when you take control.',
    unsupported: 'This kind of sandbox cannot show a live browser.',
    'stale-image': 'It is running an older image with no browser channel. Restart it to pick up the current one.',
    refused: 'Sign in again and reopen this panel.',
    lost: 'Reconnecting.',
};
