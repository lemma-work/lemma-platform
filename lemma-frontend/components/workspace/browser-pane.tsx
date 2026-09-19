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

//: A URL's host, or `null` for anything that is not one -- `about:blank`,
//: the empty string, whatever a cold browser reports before it has gone
//: anywhere. `new URL` throws on all of those, and a pane must not.
function hostOf(url: string): string | null {
    try {
        return new URL(url).host || null;
    } catch {
        return null;
    }
}

//: How often the sign-in page's anti-phishing host display is refreshed.
//: VNC carries no navigation signal of its own -- it is pixels, not events --
//: so this is what stands in for the JSON stream's old `onNavigated` message.
//: Polled whenever a site was asked for, because the pane itself needs the
//: answer now -- it is how "still opening" is told apart from "arrived", and
//: a blank page is the honest state of a browser that has not got there yet.
const NAVIGATION_POLL_MS = 1500;

//: How long the pane has to stop changing size before its display is asked to
//: match. Dragging a divider emits a resize per frame, and each one costs an X
//: mode change behind a sandbox round trip; the person only cares about where
//: they let go.
const RESIZE_SETTLE_MS = 250;

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
 * Always drivable, with nothing around it. There is no watch/drive toggle:
 * the relay takes its driving lease when somebody actually clicks or types
 * and releases it a minute after they stop, so an open pane costs the agent
 * nothing and a person never has to arm the thing before using it.
 */
export function BrowserPane({
    origin,
    conversationId,
    accessToken,
    onNavigated,
    autoResize = true,
}: {
    /** A site to steer the browser to before attaching, and the session that
     *  steer lands in: naming one means a sign-in. Without it this shows
     *  whatever this person's sandbox already has open -- VNC is the whole
     *  shared display, not a session-scoped tab, so there is nothing else to
     *  ask for. */
    origin?: string;
    /** Carried for the keepalive and the logs. It no longer picks a browser:
     *  there is one per sandbox and everything shares it. */
    conversationId?: string;
    accessToken?: string;
    /** Called with the page the browser is actually showing, polled rather
     *  than pushed -- see `NAVIGATION_POLL_MS`. Only meaningful alongside
     *  `origin`: nothing here knows the current page without one to ask the
     *  relay's `/targets` about. */
    onNavigated?: (url: string) => void;
    /**
     * Whether this viewer may reshape the sandbox display to its own box.
     *
     * One display serves the sandbox, so two viewers of different shapes
     * both asking for a fit would fight, last writer wins, and each would
     * keep seeing the other's size. A second viewer therefore watches at
     * whatever size the first has chosen and lets noVNC scale it to fit --
     * which is what `scaleViewport` is already doing for the gap between
     * asking and the resize landing.
     */
    autoResize?: boolean;
}) {
    const containerRef = useRef<HTMLDivElement>(null);
    const rfbRef = useRef<NoVncClient | null>(null);
    const [state, setState] = useState<PaneState>('connecting');
    //: The size last asked for, so a flurry of resize events is one request.
    //: A ref rather than a closure variable because it has to outlive the
    //: effect that reads it and be clearable by the one that re-runs.
    const askedSize = useRef('');
    // Whether keystrokes are actually going to the page. RFB moves focus to
    // the remote session on click by default, but "driving" being on is not
    // the same claim as "this element currently has the keyboard" -- a person
    // who takes control without having clicked the picture yet is told which
    // of the two is true rather than shown a control that quietly does
    // nothing until they discover the click on their own.
    const [keyboardIsHere, setKeyboardIsHere] = useState(false);
    //: Whether this pane has ever shown a frame. What decides between
    //: explaining itself and keeping the picture through a reconnect.
    const [hasPainted, setHasPainted] = useState(false);
    //: Where the browser actually is, polled while a site was asked for.
    const [pageUrl, setPageUrl] = useState<string | null>(null);
    //: Bumped to tear the socket down and open a new one. Reconnecting is
    //: what re-steers: `ensure_browser` points the browser at `origin` again
    //: on every connect ("arrival repeats"), and that is the only handle a
    //: person has when the answer comes an hour late and the browser it was
    //: aimed at has long since been retired.
    const [reconnectNonce, setReconnectNonce] = useState(0);

    // Steering is not instant, and until now it was not visible either.
    //
    // Opening a sign-in points a *second* Chrome -- the site's own session,
    // its own profile -- at the site, and that browser may be cold. The pane
    // meanwhile connects and paints whatever the display holds, which is a
    // blank page with a New Tab beside it. Somebody who came back to the
    // conversation an hour later clicked "Open asur.work", got exactly that,
    // and had nothing to tell them whether it was working, finished, or
    // broken. Answering late is the normal case for a question that pauses a
    // run, so it has to read as progress rather than as an empty browser.
    const steeringTo = origin ? hostOf(origin) : null;
    const arrived = !steeringTo || (!!pageUrl && hostOf(pageUrl) === steeringTo);

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
            // The new connection gets its own element; the old one stays on
            // screen until this one has a frame to replace it with.
            //
            // `container.replaceChildren()` used to run here, which wiped the
            // canvas at the *start* of every attempt. `hasPainted` was
            // supposed to mean "a reconnect keeps the picture", and it did
            // not -- it only suppressed the explanatory text, over a black
            // rectangle. A browser that had to restart left the pane black
            // for as long as that took, which reads as a crash.
            const surface = document.createElement('div');
            surface.className = 'h-full w-full';
            container.append(surface);

            // Owned here rather than handed to RFB as a URL string, purely so
            // this can read the real close code -- see `closeCodeToState`.
            // `addEventListener`, not `.onclose =`: RFB's own `attach()` sets
            // `.onclose` directly on whatever channel it is given, which
            // would silently replace a same-named assignment made here.
            const socket = new WebSocket(
                vncSocketUrl({
                    mode: 'control',
                    origin,
                    conversationId,
                    accessToken,
                }),
            );
            let closeCode = 1000;
            socket.addEventListener('close', (event) => {
                closeCode = event.code;
            });

            const rfb = new RFB(surface, socket);
            rfb.viewOnly = false;
            rfb.scaleViewport = true;
            rfb.background = 'var(--bg-canvas)';
            rfb.addEventListener('connect', () => {
                attempt = 0;
                setState('live');
                setHasPainted(true);
                // Now, and not before: whatever the previous attempt left on
                // screen was the only picture there was.
                for (const stale of Array.from(container.children)) {
                    if (stale !== surface) stale.remove();
                }
            });
            // The other half of the clipboard. `clipboardPasteFrom` sends
            // text *to* the remote; this is the remote telling us what it
            // just copied, and nothing was listening -- so copying inside the
            // agent's browser put the text precisely nowhere a person could
            // reach it. `writeText` needs the document focused and can be
            // refused outright, which is a permissions fact about the page,
            // not a broken pane.
            rfb.addEventListener('clipboard', (event?: { detail?: { text?: string } }) => {
                const text = event?.detail?.text;
                if (!text) return;
                void navigator.clipboard?.writeText(text).catch(() => undefined);
            });
            rfb.addEventListener('disconnect', () => {
                // Guarded on identity: a reconnect tears this instance down
                // and starts a new one in the same tick, and
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
                // Its canvas never painted, or has been superseded; either
                // way it must not pile up behind the next attempt.
                if (container.children.length > 1) surface.remove();
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
    }, [origin, conversationId, accessToken, reconnectNonce]);

    // Polled rather than pushed: VNC is pixels, not events, so there is no
    // message on the wire to react to the way the JSON stream's `url`
    // message let this be. Only runs when somebody asked for it and only
    // while there is a site to ask the relay about.
    useEffect(() => {
        if (!origin) return;
        let cancelled = false;
        // Cleared here rather than on `connect`: a fresh socket may be a
        // fresh browser, so the last known page says nothing about where it
        // is now -- but clearing it anywhere else leaves the pane claiming
        // "still opening" until the next tick, which is the whole interval.
        // Cleared and re-asked in the same breath.
        setPageUrl(null);
        const poll = async () => {
            try {
                const found = await getLemmaClient().workspace.browserCurrentPageUrl(origin);
                if (cancelled || !found.url) return;
                setPageUrl(found.url);
                onNavigated?.(found.url);
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
    }, [onNavigated, origin, reconnectNonce]);

    // Ask the display to be the shape of this pane, rather than scaling a
    // fixed screen into it.
    //
    // Not noVNC's `resizeSession`: `_requestRemoteResize` returns early while
    // `viewOnly` is set, and watching is the default here, so the built-in
    // path never fires for the case that needs it most. This asks over HTTP
    // instead, which also keeps the RFB input filter untouched — a resize is
    // not an input event and should not have to travel as one.
    //
    // Debounced because a person dragging the panel divider generates a
    // resize per frame, and each one is an X server mode change behind a
    // sandbox round trip.
    //
    // Re-asserted on every connect, and this is the part that was missing.
    // The last requested size used to live in a closure with `[]` deps, so it
    // survived reconnects while the display did not: after a sandbox resume,
    // an idle retirement, or an Xvfb restart the display comes back at its
    // starting size, the pane's own box never changed, no `ResizeObserver`
    // fired, and the guard said "already asked for that". The picture stayed
    // letterboxed with nothing to un-stick it short of dragging the window.
    // Depending on `state` makes each fresh `live` re-send it; the ref is
    // cleared at the same moment so the guard cannot veto that.
    useEffect(() => {
        const container = containerRef.current;
        if (!container || state !== 'live' || !autoResize) return;
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | null = null;
        askedSize.current = '';

        const fit = (width: number, height: number) => {
            const target = `${Math.round(width)}x${Math.round(height)}`;
            if (target === askedSize.current || width < 1 || height < 1) return;
            askedSize.current = target;
            void getLemmaClient()
                .workspace.browserResizeDisplay(Math.round(width), Math.round(height))
                .catch(() => {
                    // A display that would not resize is a worse fit, not a
                    // failure: the picture is still live and still scaled to
                    // fit. Let the next resize try again.
                    if (!cancelled) askedSize.current = '';
                });
        };

        // Straight away, not only on the next resize. This is the connect
        // case: the box is whatever it already was and nothing is about to
        // change it.
        const box = container.getBoundingClientRect();
        fit(box.width, box.height);

        const observer = new ResizeObserver((entries) => {
            const measured = entries[0]?.contentRect;
            if (!measured) return;
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fit(measured.width, measured.height), RESIZE_SETTLE_MS);
        });
        observer.observe(container);
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
            observer.disconnect();
        };
    }, [state, autoResize]);

    // ⌘C on a Mac reaches a Linux browser as Super+c, which copies nothing.
    // The keystroke that works over there is Ctrl+c, so the native gesture is
    // translated rather than passed through -- otherwise the person's muscle
    // memory silently does nothing, and macOS also tends to swallow the keyup
    // of a ⌘-combination, leaving the modifier stuck down on the far side.
    //
    // ⌘V is deliberately not here: the paste event below already carries the
    // text and writes it to the remote clipboard first, which is what makes
    // pasting race-free. Handling the keystroke too would paste twice.
    const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
        const rfb = rfbRef.current;
        if (!rfb || !event.metaKey || event.ctrlKey || event.altKey) return;
        const key = event.key.toLowerCase();
        if (!'cxa'.includes(key) || key.length !== 1) return;
        event.preventDefault();
        event.stopPropagation();
        rfb.sendKey(XK_CONTROL_L, 'ControlLeft', true);
        rfb.sendKey(key.charCodeAt(0), `Key${key.toUpperCase()}`, true);
        rfb.sendKey(key.charCodeAt(0), `Key${key.toUpperCase()}`, false);
        rfb.sendKey(XK_CONTROL_L, 'ControlLeft', false);
    }, []);

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
        <div className="flex h-full min-h-0 flex-col">
            <div
                className={cn(
                    'relative min-h-0 flex-1 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-canvas)] [&_canvas]:h-full [&_canvas]:w-full [&_canvas]:object-contain [&_canvas]:outline-none',
                    'cursor-crosshair',
                    // A visible edge while the keyboard is pointed here.
                    keyboardIsHere && 'ring-2 ring-[var(--action-primary)] ring-inset',
                )}
            >
                <div
                    ref={containerRef}
                    className="h-full w-full"
                    onPaste={onPaste}
                    onKeyDownCapture={onKeyDown}
                    role="application"
                    aria-label="The agent’s browser. Click and type to drive it."
                />

                {/* Only ever drawn *over* nothing. Once a frame has arrived
                    the picture stays, whatever the socket is doing: a
                    reconnect that blanked the screen and said "Connecting…"
                    read as the browser crashing, when what actually happened
                    was a two-second hiccup on a page that was still there.
                    A short-lived drop now shows the last frame, unchanged,
                    and only a pane that has never had one explains itself. */}
                {state !== 'live' && !hasPainted ? (
                    <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-canvas)] p-6">
                        <EmptyState
                            variant="region"
                            icon={<Monitor />}
                            title={TITLES[state]}
                            description={DESCRIPTIONS[state]}
                        />
                    </div>
                ) : null}
                {/* Steering in flight. Drawn over a live picture on purpose:
                    the display genuinely is showing a blank page, and saying
                    so beats letting somebody conclude the feature is broken.
                    It clears the moment the browser reports the right host,
                    so it cannot outlive the thing it describes. */}
                {state === 'live' && !arrived ? (
                    <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-canvas)]/80 p-6">
                        <div className="flex flex-col items-center gap-3">
                            <EmptyState
                                variant="region"
                                icon={<Monitor />}
                                title={`Opening ${steeringTo}…`}
                                description="Pointing the browser at the site. A browser that has been idle takes a moment to start."
                            />
                            <Button
                                variant="secondary"
                                size="sm"
                                onClick={() => setReconnectNonce((n) => n + 1)}
                            >
                                Try again
                            </Button>
                        </div>
                    </div>
                ) : null}
                {/* A kept picture is not a live one, and a reconnect that
                    lasts -- a browser that has to restart takes tens of
                    seconds -- must not look like a page sitting idle. Small
                    and out of the way, because the frame underneath is still
                    the most useful thing on screen. */}
                {state !== 'live' && hasPainted && !TERMINAL_STATES.has(state) ? (
                    <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-[var(--surface-1)]/95 px-3 py-1 text-xs text-[var(--text-secondary)] shadow-[var(--shadow-xs)]">
                        {state === 'no-browser' ? 'Restarting the browser…' : 'Reconnecting…'}
                    </div>
                ) : null}
                {/* Terminal states are the exception: "you are not signed in"
                    or "this image has no VNC" will not fix themselves, and a
                    stale picture over them is a lie. */}
                {TERMINAL_STATES.has(state) && hasPainted ? (
                    <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-canvas)]/90 p-6">
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
