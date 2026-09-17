'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { Monitor } from '@/components/ui/icons';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { reconnectDelayMs, vncSocketUrl } from '@/lib/workspace/browser-view';
import { cn } from '@/lib/utils';

import type NoVncClient from '@novnc/novnc';

type PaneState = 'connecting' | 'live' | 'lost' | 'refused' | 'no-browser' | 'unsupported' | 'stale-image';

//: Why a socket closed, in numbers a client can branch on -- matches
//: `browser_view_controller.py`'s `CLOSE_*` constants exactly. Read off the
//: WebSocket's own `close` event, not RFB's `disconnect` event: RFB reports
//: only `{clean: boolean}`, which cannot tell "the browser is not running"
//: (retrying is right -- the agent may start one) from "you are not signed
//: in" (retrying with the same expired token can never succeed). Losing this
//: distinction was a real regression from the JPEG pane, which had it: every
//: disconnect retried forever, including an expired session, which looked
//: exactly like "the connection dropped" repeating with no way out.
const CLOSE_UNAUTHENTICATED = 4401;
const CLOSE_ORIGIN_REFUSED = 4403;
const CLOSE_NO_BROWSER = 4409;
const CLOSE_UNSUPPORTED = 4422;
const CLOSE_STALE_IMAGE = 4426;

const closeCodeToState = (code: number): PaneState => {
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

//: States a fresh connection attempt cannot fix: wrong or expired
//: credentials, a fabric that cannot do this at all, or an image missing the
//: relay. Retrying will not change any of these until something outside this
//: component does -- signing in again, replacing the image -- so retrying
//: forever just repeats the same failure while telling the person otherwise.
const TERMINAL_STATES: ReadonlySet<PaneState> = new Set(['refused', 'unsupported', 'stale-image']);

//: X11 keysyms for the two keys a synthetic paste needs. Lowercase ASCII
//: letters are their own keysym in this space, so `v` needs no table lookup.
const XK_CONTROL_L = 0xffe3;
const XK_LOWER_V = 0x76;

/**
 * Ctrl+V, sent as real key events, once the remote clipboard already holds
 * the text.
 *
 * `clipboardPasteFrom` only sets the VNC clipboard -- it does not type
 * anything, the same way copying something to your own clipboard does not
 * paste it anywhere by itself. Letting the browser's own Ctrl+V reach the
 * remote session through RFB's ordinary keyboard capture races that write: a
 * keydown can arrive at the server before the clipboard message does, and
 * paste whatever the remote clipboard held a moment earlier -- which is what
 * "paste doesn't work" usually was. Sending the clipboard write and then this
 * keystroke, in that order, from the same place, is what removes the race
 * rather than narrowing it.
 */
function sendCtrlV(rfb: NoVncClient): void {
    rfb.sendKey(XK_CONTROL_L, 'ControlLeft', true);
    rfb.sendKey(XK_LOWER_V, 'KeyV', true);
    rfb.sendKey(XK_LOWER_V, 'KeyV', false);
    rfb.sendKey(XK_CONTROL_L, 'ControlLeft', false);
}

//: How often the sign-in page's anti-phishing host display is refreshed.
//: VNC carries no navigation signal of its own -- it is pixels, not events --
//: so this is what stands in for the JSON stream's old `onNavigated` message.
//: Only polled when `onNavigated` is actually passed, which today is only the
//: sign-in page: an ordinary watch/drive pane has nothing that reads it.
const NAVIGATION_POLL_MS = 1500;

/**
 * The agent's browser, live, over VNC.
 *
 * A real X11 display rather than a screenshot pipeline: what is on screen and
 * where a click lands are the same numbers noVNC already uses internally, so
 * there is no coordinate space here to get wrong -- unlike the CDP-JPEG
 * pipeline this replaced, which needed a client/picture/page coordinate
 * translation for every input event and still mis-clicked. Paste is a real
 * synced clipboard rather than a synthesized keystroke, for the same reason.
 *
 * Watching by default. Driving is a deliberate act — the toggle exists so that
 * a person reading a page cannot type into it by accident, and so that the
 * thing they are about to do is named before they do it.
 */
export function BrowserPane({
    origin,
    accessToken,
    autoControl = false,
    onNavigated,
}: {
    /** A site to steer the browser to before attaching, and the session that
     *  steer lands in: naming one means a sign-in. Without it this shows
     *  whatever this person's sandbox already has open -- VNC is the whole
     *  shared display, not a session-scoped tab, so there is nothing else to
     *  ask for. */
    origin?: string;
    accessToken?: string;
    autoControl?: boolean;
    /** Called with the page the browser is actually showing, polled rather
     *  than pushed -- see `NAVIGATION_POLL_MS`. Only meaningful alongside
     *  `origin`: nothing here knows the current page without one to ask the
     *  relay's `/targets` about. */
    onNavigated?: (url: string) => void;
}) {
    const containerRef = useRef<HTMLDivElement>(null);
    const rfbRef = useRef<NoVncClient | null>(null);
    const [state, setState] = useState<PaneState>('connecting');
    const [controlling, setControlling] = useState(autoControl);
    // Whether keystrokes are actually going to the page. RFB moves focus to
    // the remote session on click by default, but "driving" being on is not
    // the same claim as "this element currently has the keyboard" -- a person
    // who takes control without having clicked the picture yet is told which
    // of the two is true rather than shown a control that quietly does
    // nothing until they discover the click on their own.
    const [keyboardIsHere, setKeyboardIsHere] = useState(false);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        let cancelled = false;
        let attempt = 0;
        let retryTimer: ReturnType<typeof setTimeout> | null = null;

        const connect = async () => {
            if (cancelled) return;
            setState('connecting');
            // Loaded on connect rather than imported at module scope: the
            // library reaches for `document`/`WebSocket` at import time, which
            // a server render has neither of.
            let RFB: typeof NoVncClient;
            try {
                ({ default: RFB } = await import('@novnc/novnc'));
            } catch {
                if (cancelled) return;
                setState('lost');
                retryTimer = setTimeout(connect, reconnectDelayMs(attempt++));
                return;
            }
            if (cancelled) return;
            container.replaceChildren();

            // Owned here rather than handed to RFB as a URL string, purely so
            // this can read the real close code -- see `closeCodeToState`.
            // `addEventListener`, not `.onclose =`: RFB's own `attach()` sets
            // `.onclose` directly on whatever channel it is given, which
            // would silently replace a same-named assignment made here.
            const socket = new WebSocket(
                vncSocketUrl({ mode: controlling ? 'control' : 'view', origin, accessToken }),
            );
            let closeCode = 1000;
            socket.addEventListener('close', (event) => {
                closeCode = event.code;
            });

            const rfb = new RFB(container, socket);
            rfb.viewOnly = !controlling;
            rfb.scaleViewport = true;
            rfb.background = 'var(--bg-canvas)';
            rfb.addEventListener('connect', () => {
                attempt = 0;
                setState('live');
            });
            rfb.addEventListener('disconnect', () => {
                // Guarded on identity: toggling `controlling` tears this
                // instance down and starts a new one in the same tick, and
                // the socket closing does not happen synchronously with
                // that -- the resulting event arrives after the new instance
                // is already the one in `rfbRef`. Without this check, that
                // late event nulled a *live* ref out from under it, so every
                // paste and keystroke after the first "Take control" landed
                // on `rfbRef.current === null` while the picture kept
                // rendering the new connection's frames regardless -- there
                // was nothing wrong to see, only a ref pointing at nothing.
                if (rfbRef.current !== rfb) return;
                rfbRef.current = null;
                if (cancelled) return;
                const next = closeCodeToState(closeCode);
                setState(next);
                setKeyboardIsHere(false);
                if (TERMINAL_STATES.has(next)) return;
                retryTimer = setTimeout(connect, reconnectDelayMs(attempt++));
            });
            rfbRef.current = rfb;
        };

        // `focusin`/`focusout`, not RFB events -- it dispatches neither. What
        // actually happens on click is `canvas.focus()`, a real DOM focus
        // change, and these are that change's bubbling form. Native listeners
        // rather than a prop on the canvas because RFB owns that element; it
        // is created and destroyed inside `connect()`, so the container is
        // the one thing here with a stable identity to listen on.
        const onFocusIn = () => setKeyboardIsHere(true);
        const onFocusOut = () => setKeyboardIsHere(false);
        container.addEventListener('focusin', onFocusIn);
        container.addEventListener('focusout', onFocusOut);

        connect();
        return () => {
            cancelled = true;
            container.removeEventListener('focusin', onFocusIn);
            container.removeEventListener('focusout', onFocusOut);
            if (retryTimer) clearTimeout(retryTimer);
            rfbRef.current?.disconnect();
            rfbRef.current = null;
        };
    }, [controlling, origin, accessToken]);

    // Polled rather than pushed: VNC is pixels, not events, so there is no
    // message on the wire to react to the way the JSON stream's `url`
    // message let this be. Only runs when somebody asked for it and only
    // while there is a site to ask the relay about.
    useEffect(() => {
        if (!onNavigated || !origin) return;
        let cancelled = false;
        const poll = async () => {
            try {
                const found = await getLemmaClient().workspace.browserCurrentPageUrl(origin);
                if (!cancelled && found.url) onNavigated(found.url);
            } catch {
                // Best effort: a missed poll is a stale host label for
                // another `NAVIGATION_POLL_MS`, not a reason to stop.
            }
        };
        const interval = setInterval(poll, NAVIGATION_POLL_MS);
        poll();
        return () => {
            cancelled = true;
            clearInterval(interval);
        };
    }, [onNavigated, origin]);

    const onPaste = useCallback((event: React.ClipboardEvent<HTMLDivElement>) => {
        const rfb = rfbRef.current;
        if (!rfb || rfb.viewOnly) return;
        const text = event.clipboardData.getData('text');
        if (!text) return;
        event.preventDefault();
        rfb.clipboardPasteFrom(text);
        sendCtrlV(rfb);
    }, []);

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

            <div
                className={cn(
                    'relative min-h-0 flex-1 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-canvas)] [&_canvas]:h-full [&_canvas]:w-full [&_canvas]:object-contain [&_canvas]:outline-none',
                    controlling && 'cursor-crosshair',
                    // A visible edge while the keyboard is pointed here.
                    controlling && keyboardIsHere && 'ring-2 ring-[var(--action-primary)] ring-inset',
                )}
            >
                <div
                    ref={containerRef}
                    className="h-full w-full"
                    onPaste={onPaste}
                    role={controlling ? 'application' : 'img'}
                    aria-label={
                        controlling
                            ? 'The agent’s browser. Click and type to drive it.'
                            : 'The agent’s browser, live'
                    }
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
        </div>
    );
}

const TITLES: Record<PaneState, string> = {
    connecting: 'Connecting…',
    live: '',
    'no-browser': 'The browser is not running',
    unsupported: 'Not available on this computer',
    'stale-image': 'This computer needs restarting',
    refused: 'You are not signed in',
    lost: 'The connection dropped',
};

const DESCRIPTIONS: Record<PaneState, string> = {
    connecting: 'Waking the computer and starting its browser. The first time takes a moment.',
    live: '',
    'no-browser': 'It starts when the agent opens a page, or when you take control.',
    unsupported: 'This kind of sandbox cannot show a live browser.',
    'stale-image':
        'It is running an older image with no VNC channel. Restart it to pick up the current one.',
    refused: 'Sign in again and reopen this panel.',
    lost: 'Reconnecting.',
};
