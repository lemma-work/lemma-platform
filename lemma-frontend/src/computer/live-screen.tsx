"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type RFBClient from "@novnc/novnc";
import { apiUrl, sessionToken } from "@/session/client";
import { useBrowserResize } from "./queries";
import { isSettled, retryDelay, socketUrl, stateFromClose, type LiveState } from "./live";

/** The sandbox's own display, live, in this pane.
 *
 *  VNC rather than a frame: the proxied page refuses to be framed by this
 *  app's origin, and pixels over a socket served from the API's own host are
 *  what the platform settled on instead. `@novnc/novnc` owns the rendering,
 *  the scaling and the input capture, so there is no frame protocol here and
 *  no coordinate space to get wrong.
 *
 *  Two callers, one pane. Watching the machine is `view`, where the relay
 *  drops every keyboard and pointer message on the way past — enforced there
 *  rather than here, message by message, because a viewer that is not noVNC
 *  could otherwise smuggle a keystroke behind a legitimate frame. Signing in
 *  is `control`, where the whole point is that a person types.
 *
 *  Attaching **starts a browser if none is running and holds the machine awake
 *  for as long as it is open** — the idle sweep measures from the last request,
 *  and watching is not one. So this mounts on a click and unmounts the moment
 *  nobody is looking at it. It must never be rendered speculatively.
 */

/** X11 keysyms for the two keys a synthetic paste needs. A lowercase ASCII
 *  letter is its own keysym in this space, so `v` needs no lookup. */
const XK_CONTROL_L = 0xffe3;
const XK_LOWER_V = 0x76;

/** Ctrl+V, as real key events, once the far clipboard already holds the text.
 *
 *  `clipboardPasteFrom` only sets the remote clipboard — it types nothing, the
 *  same way copying something here does not paste it anywhere by itself. And
 *  letting the browser's own Ctrl+V through RFB's ordinary keyboard capture
 *  races that write: the keydown can reach the server before the clipboard
 *  message does, and paste whatever was there a moment earlier. Sending the
 *  write and then this, in that order, from one place, removes the race rather
 *  than narrowing it.
 */
function sendCtrlV(rfb: RFBClient): void {
    rfb.sendKey(XK_CONTROL_L, "ControlLeft", true);
    rfb.sendKey(XK_LOWER_V, "KeyV", true);
    rfb.sendKey(XK_LOWER_V, "KeyV", false);
    rfb.sendKey(XK_CONTROL_L, "ControlLeft", false);
}

/** What the display-size route will accept. Clamped here so a pane in an
 *  awkward layout degrades to the nearest size it can have rather than being
 *  refused outright for asking. */
const MIN_WIDTH = 320;
const MIN_HEIGHT = 240;
const MAX_SIDE = 4096;

/** How long the pane has to stop changing size before its display is asked to
 *  match. Dragging a divider emits a resize a frame, and each one costs a mode
 *  change behind a sandbox round trip; only where somebody let go matters. */
const RESIZE_SETTLE_MS = 250;

export function LiveScreen({ mode, origin, conversationId, reopen = 0, autoResize = true, onState }: {
    mode: "view" | "control";
    /** A site to steer the browser to before attaching. Naming one means a
     *  sign-in, and the steer repeats on every connect. */
    origin?: string | null;
    conversationId?: string | null;
    /** Bumped to drop this socket and open another.
     *
     *  Reconnecting is what re-steers: the server points the browser at
     *  `origin` again on every connect, so this is the one handle somebody
     *  has when the answer comes an hour late and the browser it was aimed at
     *  has long since been retired. Without it a pane stuck on "Opening…"
     *  had nothing to offer but reloading the page. */
    reopen?: number;
    /** Whether this pane may reshape the sandbox display to its own box.
     *
     *  One display serves the machine, so two panes of different shapes both
     *  asking for a fit would fight, last writer wins, and each would keep
     *  seeing the other's size. */
    autoResize?: boolean;
    onState: (state: LiveState) => void;
}) {
    const holder = useRef<HTMLDivElement>(null);
    const client = useRef<RFBClient | null>(null);
    /* The callback is read through a ref so that a parent re-rendering with a
       new closure does not tear down a live connection and start another. */
    const report = useRef(onState);
    report.current = onState;
    /* Whether keystrokes are actually going to the page. RFB moves focus to
       the remote session when the picture is clicked, and "this pane can
       drive" is not the same claim as "this element has the keyboard" —
       somebody who has not clicked yet is told which of the two is true rather
       than left typing into nothing. */
    const [typing, setTyping] = useState(false);
    /* Whether there is a live connection right now, held here as well as
       reported outward because the resize below has to re-ask on every one of
       them — see that effect for what a display forgets when it restarts. */
    const [connected, setConnected] = useState(false);

    const resize = useBrowserResize();
    const askSize = useRef(resize.mutate);
    askSize.current = resize.mutate;

    useEffect(() => {
        const container = holder.current;
        if (!container) return;

        let stopped = false;
        let attempt = 0;
        let timer: ReturnType<typeof setTimeout> | null = null;

        const connect = async () => {
            if (stopped) return;
            report.current("connecting");

            /* Imported here rather than at module scope: the library reaches
               for `document` and `WebSocket` as it loads, and it has no
               business being in the bundle of a pane nobody has opened. */
            let RFB: typeof RFBClient;
            try {
                ({ default: RFB } = await import("@novnc/novnc"));
            } catch {
                if (stopped) return;
                report.current("lost");
                timer = setTimeout(connect, retryDelay(attempt++));
                return;
            }
            if (stopped) return;


            const surface = document.createElement("div");
            surface.className = "screen-surface";
            container.append(surface);

            /* The socket is made here and handed over, rather than letting RFB
               open one from a URL, purely so the close code can be read. And
               `addEventListener` rather than `.onclose =`, because RFB assigns
               `.onclose` on whatever channel it is given and would quietly
               replace an assignment made here. */
            const socket = new WebSocket(socketUrl(apiUrl(), {
                mode, origin, conversationId, accessToken: sessionToken(),
            }));
            let closed = 1000;
            socket.addEventListener("close", (event) => { closed = event.code; });

            const rfb = new RFB(surface, socket);
            client.current = rfb;
            rfb.viewOnly = mode === "view";
            rfb.scaleViewport = true;
            rfb.background = "transparent";
            rfb.addEventListener("connect", () => {
                if (stopped) return;
                attempt = 0;
                setConnected(true);
                report.current("live");
                /* Now, and not before: everything else in here is the previous
                   attempt's picture, which was worth keeping right up to the
                   moment there was a new one. */
                for (const stale of Array.from(container.children)) {
                    if (stale !== surface) stale.remove();
                }
            });
            /* The other half of the clipboard: the far side telling us what it
               just copied. Without this, copying something inside the
               teammate's browser puts it precisely nowhere a person can reach.
               `writeText` wants the document focused and may be refused
               outright, which is a fact about this page's permissions and not
               a broken pane, so it fails quietly. */
            rfb.addEventListener("clipboard", (event?: { detail?: { text?: string } }) => {
                const text = event?.detail?.text;
                if (text) void navigator.clipboard?.writeText(text).catch(() => undefined);
            });
            rfb.addEventListener("disconnect", () => {
                /* Guarded on identity: a teardown starts nothing new, but a
                   late event from a previous socket must not speak for the
                   connection that replaced it. */
                if (client.current !== rfb) return;
                client.current = null;

                if (container.children.length > 1) surface.remove();
                if (stopped) return;
                setTyping(false);
                setConnected(false);
                const state = stateFromClose(closed);
                report.current(state);
                if (!isSettled(state)) timer = setTimeout(connect, retryDelay(attempt++));
            });
        };

        /* `focusin`/`focusout` rather than an RFB event, because it dispatches
           neither: what happens on a click is a real DOM focus move onto the
           canvas, and these are that move's bubbling form. On the container,
           because RFB owns the canvas — it makes and destroys it inside
           `connect` — so the container is the one stable thing to listen on. */
        const focused = () => setTyping(true);
        const blurred = () => setTyping(false);
        container.addEventListener("focusin", focused);
        container.addEventListener("focusout", blurred);

        void connect();
        return () => {
            stopped = true;
            container.removeEventListener("focusin", focused);
            container.removeEventListener("focusout", blurred);
            if (timer) clearTimeout(timer);
            /* Here as well as in `disconnect`, and it has to be: a teardown
               nulls the ref first, so the event this socket eventually fires
               is guarded out. Without this a re-steer would keep `connected`
               true across the swap, the resize below would never re-run, and
               the new connection would inherit the old one's "already asked
               for that". */
            setConnected(false);
            /* Disconnecting is the whole point of this teardown: the socket is
               what holds the machine awake, so a pane left connected after
               nobody is looking runs a sandbox for nothing. */
            try { client.current?.disconnect(); } catch { /* already gone */ }
            client.current = null;
        };
    }, [mode, origin, conversationId, reopen]);


    useEffect(() => {
        const container = holder.current;
        if (!container || !autoResize || !connected) return;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let asked = "";

        const fit = (width: number, height: number) => {
            const wanted = width + "x" + height;
            if (wanted === asked) return;
            asked = wanted;
            askSize.current({ width, height }, { onError: () => { asked = ""; } });
        };
        const clamp = (box: { width: number; height: number }) => ({
            width: Math.round(Math.min(Math.max(box.width, MIN_WIDTH), MAX_SIDE)),
            height: Math.round(Math.min(Math.max(box.height, MIN_HEIGHT), MAX_SIDE)),
        });

        /* Straight away rather than on the next resize: this is the connect
           case, where the box is whatever it already was and nothing is about
           to change it. */
        const now = clamp(container.getBoundingClientRect());
        fit(now.width, now.height);

        const watcher = new ResizeObserver(([entry]) => {
            const box = entry?.contentRect;
            if (!box) return;
            const next = clamp(box);
            if (next.width + "x" + next.height === asked) return;
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fit(next.width, next.height), RESIZE_SETTLE_MS);
        });
        watcher.observe(container);
        return () => { watcher.disconnect(); if (timer) clearTimeout(timer); };
    }, [autoResize, connected]);

    /* The keystroke that works over there is Ctrl, so the native gesture is
       translated rather than passed through — otherwise somebody's muscle
       memory silently does nothing, and macOS tends to swallow the keyup of a
       Cmd combination besides, leaving the modifier stuck down on the far
       side. Cmd+V is deliberately not here: the paste below already carries
       the text and writes it across first, which is what makes it race-free,
       and handling the keystroke too would paste twice. */
    const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
        const rfb = client.current;
        if (!rfb || rfb.viewOnly || !event.metaKey || event.ctrlKey || event.altKey) return;
        const key = event.key.toLowerCase();
        if (key.length !== 1 || !"cxa".includes(key)) return;
        event.preventDefault();
        event.stopPropagation();
        rfb.sendKey(XK_CONTROL_L, "ControlLeft", true);
        rfb.sendKey(key.charCodeAt(0), "Key" + key.toUpperCase(), true);
        rfb.sendKey(key.charCodeAt(0), "Key" + key.toUpperCase(), false);
        rfb.sendKey(XK_CONTROL_L, "ControlLeft", false);
    }, []);

    const onPaste = useCallback((event: React.ClipboardEvent<HTMLDivElement>) => {
        const rfb = client.current;
        if (!rfb || rfb.viewOnly) return;
        const text = event.clipboardData.getData("text");
        if (!text) return;
        event.preventDefault();
        rfb.clipboardPasteFrom(text);
        sendCtrlV(rfb);
    }, []);

    return (
        <div className="screen-live" ref={holder} onKeyDown={onKeyDown} onPaste={onPaste}>
            {/* Said only where it is both true and actionable: this pane can
                drive, and the keyboard is not in it yet. A password typed at a
                picture that was never listening is the failure worth one line
                of chrome to prevent. */}
            {mode === "control" && !typing && <p className="screen-hint">Click the page to type in it</p>}
        </div>
    );
}
