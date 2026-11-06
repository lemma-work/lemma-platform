"use client";

import { useEffect, useRef, useState } from "react";
import { apiUrl, sessionToken } from "@/session/client";
import { copyText } from "@/desktop/clipboard";
import { isSettled, retryDelay, socketUrl, stateFromClose, type LiveState } from "./live";
import { RtcSession, type CursorShape, type StreamStats } from "./rtc-session";
import {
    WheelSteps, buttonFor, isMac, keyBelongsToMachine, keysymFor, pasteMessage, textPieces, toScreen,
    type FallbackReason,
} from "./stream";

/** The machine's screen over WebRTC: a video, and a data channel for input.
 *
 *  The same props and the same promises as `LiveScreen`, so a pane can hold
 *  either. It differs underneath: the picture is H.264 or VP8 from the
 *  machine's streamer, through TURN, not VNC rectangles through the API — a
 *  push stream at a few megabits instead of a pulled one at tens, which is
 *  what makes a slow network usable at all. When ICE cannot connect within
 *  five seconds of the machine's config arriving it says so through
 *  `onFallback`, and the pane uses VNC.
 *
 *  Watching sends nothing. Driving sends the streamer's closed message set —
 *  pointer, button, wheel, key, text, paste — over a channel the streamer
 *  accepts only from the one viewer whose ticket says `control`. That decision
 *  is the API's and the streamer's; this pane only asks.
 *
 *  The cursor is drawn here, by this browser, from the shape the machine
 *  sends: a pointer that waits a round trip to move is the first thing that
 *  makes a remote screen feel remote.
 */
export function LiveScreenRtc({
    mode, origin, conversationId, reopen = 0, viewer, wantControl, onState, onFallback, onControl, onAgentDriving, onStats,
}: {
    mode: "view" | "control";
    origin?: string | null;
    conversationId?: string | null;
    reopen?: number;
    viewer: string;
    /** Whether this pane wants to drive. Changing it takes or gives back
     *  control on the open connection; nothing reconnects. */
    wantControl: boolean;
    onState: (state: LiveState) => void;
    onFallback: (reason: FallbackReason) => void;
    onControl?: (driving: boolean, mayDrive: boolean) => void;
    onAgentDriving?: (driving: boolean) => void;
    /** Sampled while live, for the beacon. */
    onStats?: (stats: StreamStats) => void;
}) {
    const holder = useRef<HTMLDivElement>(null);
    const video = useRef<HTMLVideoElement>(null);
    const keyboard = useRef<HTMLTextAreaElement>(null);
    const session = useRef<RtcSession | null>(null);
    const handlers = useRef({ onState, onFallback, onControl, onAgentDriving, onStats });
    handlers.current = { onState, onFallback, onControl, onAgentDriving, onStats };
    const screen = useRef({ width: 0, height: 0 });
    const cursors = useRef(new Map<number, CursorShape>());
    const [cursor, setCursor] = useState<CursorShape | null>(null);
    const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
    const [driving, setDriving] = useState(false);
    const [typing, setTyping] = useState(false);

    useEffect(() => {
        let stopped = false;
        let attempt = 0;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let sampler: ReturnType<typeof setInterval> | null = null;

        const connect = () => {
            if (stopped) return;
            handlers.current.onState("connecting");
            const url = socketUrl(apiUrl(), {
                mode, origin, conversationId, accessToken: sessionToken(), transport: "rtc", viewer,
            });
            const current: RtcSession = new RtcSession(url, {
                onStream: (stream) => {
                    const element = video.current;
                    if (!element) return;
                    element.srcObject = stream;
                    void element.play().catch(() => undefined);
                },
                onLive: () => {
                    attempt = 0;
                    if (sampler) clearInterval(sampler);
                    sampler = setInterval(() => {
                        void current.stats().then((stats) => handlers.current.onStats?.(stats));
                    }, 2_000);
                },
                onControl: (isDriving, mayDrive) => {
                    setDriving(isDriving);
                    handlers.current.onControl?.(isDriving, mayDrive);
                },
                onCursor: (shape) => {
                    const known = shape.png ? shape : { ...cursors.current.get(shape.id), ...shape };
                    if (shape.png) cursors.current.set(shape.id, shape);
                    setCursor(known.png ? known : null);
                },
                onPointer: (x, y) => setPointer({ x, y }),
                onScreenSize: (width, height) => { screen.current = { width, height }; },
                onClipboard: (text) => { void copyText(text).catch(() => undefined); },
                onAgentDriving: (isDriving) => handlers.current.onAgentDriving?.(isDriving),
                onFallback: (reason) => {
                    if (stopped) return;
                    handlers.current.onFallback(reason);
                },
                onClosed: (code) => {
                    if (stopped || session.current !== current) return;
                    session.current = null;
                    if (sampler) clearInterval(sampler);
                    setDriving(false);
                    const state = stateFromClose(code);
                    handlers.current.onState(state);
                    if (!isSettled(state)) timer = setTimeout(connect, retryDelay(attempt++));
                },
            });
            session.current = current;
            /* The stream bench's probe (`stream-probe.ts`), as on the VNC
               pane: dev builds only, behind `?streamProbe=1`. */
            if (process.env.NODE_ENV !== "production" && holder.current) {
                const surface = holder.current;
                const socketAt = performance.timeOrigin + performance.now();
                void import("./stream-probe").then((probe) => {
                    if (probe.streamProbeAsked()) {
                        probe.attachStreamProbe({ socket: current.signalling, surface, socketAt, mediaBytes: () => current.bytesReceived() });
                    }
                });
            }
        };

        connect();
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
            if (sampler) clearInterval(sampler);
            session.current?.close();
            session.current = null;
        };
    }, [mode, origin, conversationId, reopen, viewer]);

    /* Take or give back control on the connection that is already open. */
    useEffect(() => {
        const current = session.current;
        if (!current) return;
        if (wantControl && !current.isDriving) current.take();
        if (!wantControl && current.isDriving) current.release();
    }, [wantControl]);

    /* The picture counts as live once a frame is on screen, not when ICE says
       connected: "live" over a black rectangle is the failure this avoids. */
    useEffect(() => {
        const element = video.current;
        if (!element) return;
        const shown = () => handlers.current.onState("live");
        element.addEventListener("loadeddata", shown);
        return () => element.removeEventListener("loadeddata", shown);
    }, []);

    useInput({ holder, video, keyboard, session, screen, driving, setTyping });

    const hotspot = cursor ? `${cursor.x} ${cursor.y}` : "";
    const cursorStyle = driving && cursor?.png ? `url(data:image/png;base64,${cursor.png}) ${hotspot}, default` : undefined;

    return (
        <div
            className={"screen-live screen-live--rtc" + (driving ? " screen-live--driving" : "")}
            ref={holder}
            style={{ cursor: cursorStyle }}
        >
            <video ref={video} className="screen-video" autoPlay muted playsInline disablePictureInPicture />
            {/* Watching, the machine's own pointer is drawn where the machine
                says it is — so the agent's mouse is visible. Driving, the
                browser's cursor is the pointer, and this is not drawn. */}
            {!driving && cursor?.png && pointer && <RemotePointer cursor={cursor} pointer={pointer} screen={screen.current} holder={holder} />}
            {/* The keyboard's target: focused on a click, so keys and an IME
                go to the machine, and on a phone it is what raises the
                on-screen keyboard. Never shown. */}
            <textarea
                ref={keyboard}
                className="screen-keys"
                aria-label="Type into the machine's browser"
                autoCapitalize="off"
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                tabIndex={-1}
            />
            {mode === "control" && driving && !typing && <p className="screen-hint">Click the page to type in it</p>}
        </div>
    );
}

function RemotePointer({ cursor, pointer, screen, holder }: {
    cursor: CursorShape;
    pointer: { x: number; y: number };
    screen: { width: number; height: number };
    holder: React.RefObject<HTMLDivElement | null>;
}) {
    const box = holder.current?.getBoundingClientRect();
    if (!box || !screen.width || !screen.height) return null;
    const scale = Math.min(box.width / screen.width, box.height / screen.height);
    const left = (box.width - screen.width * scale) / 2 + (pointer.x - cursor.x) * scale;
    const top = (box.height - screen.height * scale) / 2 + (pointer.y - cursor.y) * scale;
    return (
        <img
            className="screen-pointer"
            alt=""
            src={`data:image/png;base64,${cursor.png}`}
            style={{ left, top, width: (cursor.w ?? 0) * scale || undefined }}
        />
    );
}

/** Pointer, wheel, keys, text and paste, from this pane to the machine.
 *
 *  Pointer moves are sent at most once a frame — the machine cannot show
 *  them any faster, and a trackpad fires several per frame. Touch is the
 *  mouse: a tap clicks, a drag drags. Two fingers zoom the picture here, and
 *  only here: a pinch is how a person reads small text on a phone, not
 *  something the page should see.
 */
function useInput({ holder, video, keyboard, session, screen, driving, setTyping }: {
    holder: React.RefObject<HTMLDivElement | null>;
    video: React.RefObject<HTMLVideoElement | null>;
    keyboard: React.RefObject<HTMLTextAreaElement | null>;
    session: React.RefObject<RtcSession | null>;
    screen: React.RefObject<{ width: number; height: number }>;
    driving: boolean;
    setTyping: (typing: boolean) => void;
}) {
    useEffect(() => {
        const box = holder.current;
        const keys = keyboard.current;
        const picture = video.current;
        if (!box || !keys || !picture || !driving) return;
        const mac = isMac();
        const wheel = new WheelSteps();
        const touches = new Map<number, { x: number; y: number }>();
        let zoom = 1;
        let pinchFrom = 0;
        let pendingMove: { x: number; y: number } | null = null;
        let frame = 0;

        const send = (message: Parameters<RtcSession["send"]>[0]) => session.current?.send(message);
        const where = (event: { clientX: number; clientY: number }) => {
            const size = screen.current;
            const shown = { width: picture.videoWidth || size.width, height: picture.videoHeight || size.height };
            return toScreen(event.clientX, event.clientY, picture.getBoundingClientRect(), shown);
        };
        const flushMove = () => {
            frame = 0;
            if (pendingMove) send({ t: "m", ...pendingMove });
            pendingMove = null;
        };

        const onPointerMove = (event: PointerEvent) => {
            if (event.pointerType === "touch" && touches.size > 1) return pinch(event);
            const point = where(event);
            if (!point) return;
            pendingMove = point;
            if (!frame) frame = requestAnimationFrame(flushMove);
        };
        const onPointerDown = (event: PointerEvent) => {
            if (event.pointerType === "touch") {
                touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
                if (touches.size > 1) return;
            }
            const point = where(event);
            const button = buttonFor(event.button);
            if (!point || !button) return;
            event.preventDefault();
            box.setPointerCapture(event.pointerId);
            keys.focus({ preventScroll: true });
            flushMove();
            send({ t: "m", ...point });
            send({ t: "b", b: button, d: true });
        };
        const onPointerUp = (event: PointerEvent) => {
            const wasPinch = touches.size > 1;
            touches.delete(event.pointerId);
            if (wasPinch) { pinchFrom = 0; return; }
            const button = buttonFor(event.button);
            if (!button) return;
            flushMove();
            send({ t: "b", b: button, d: false });
        };
        const pinch = (event: PointerEvent) => {
            touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
            const [a, b] = Array.from(touches.values());
            if (!a || !b) return;
            const distance = Math.hypot(a.x - b.x, a.y - b.y);
            if (!pinchFrom) { pinchFrom = distance / zoom; return; }
            zoom = Math.min(4, Math.max(1, distance / pinchFrom));
            picture.style.transform = zoom === 1 ? "" : `scale(${zoom})`;
        };
        const onWheel = (event: WheelEvent) => {
            event.preventDefault();
            const steps = wheel.add(event.deltaX, event.deltaY, event.deltaMode);
            if (steps) send(steps);
        };
        const onContextMenu = (event: Event) => event.preventDefault();

        const sendKey = (event: KeyboardEvent, down: boolean) => {
            if (event.isComposing || !keyBelongsToMachine(event, { mac })) return;
            const keysym = keysymFor(event, { mac });
            if (keysym === null) return;
            /* Handled here, so the textarea never receives the character and
               `beforeinput` below never sends it a second time. */
            event.preventDefault();
            send({ t: "k", k: keysym, d: down });
        };
        const onKeyDown = (event: KeyboardEvent) => sendKey(event, true);
        const onKeyUp = (event: KeyboardEvent) => sendKey(event, false);
        /* What a keydown could not say: an IME's composed text, and a phone's
           on-screen keyboard, which reports "Unidentified" keys and delivers
           its characters as input. Sent as text, never as keystrokes. */
        const onBeforeInput = (event: InputEvent) => {
            if (event.isComposing) return;
            event.preventDefault();
            if (event.inputType === "insertText" && event.data) {
                for (const piece of textPieces(event.data)) send({ t: "txt", s: piece });
            } else if (event.inputType === "insertLineBreak" || event.inputType === "insertParagraph") {
                send({ t: "k", k: 0xff0d, d: true });
                send({ t: "k", k: 0xff0d, d: false });
            } else if (event.inputType === "deleteContentBackward") {
                send({ t: "k", k: 0xff08, d: true });
                send({ t: "k", k: 0xff08, d: false });
            }
        };
        const onCompositionEnd = (event: CompositionEvent) => {
            for (const piece of textPieces(event.data ?? "")) send({ t: "txt", s: piece });
            keys.value = "";
        };
        const onPaste = (event: ClipboardEvent) => {
            const message = pasteMessage(event.clipboardData?.getData("text") ?? "");
            if (!message) return;
            event.preventDefault();
            send(message);
        };
        const onFocus = () => setTyping(true);
        const onBlur = () => setTyping(false);

        box.addEventListener("pointermove", onPointerMove);
        box.addEventListener("pointerdown", onPointerDown);
        box.addEventListener("pointerup", onPointerUp);
        box.addEventListener("pointercancel", onPointerUp);
        box.addEventListener("wheel", onWheel, { passive: false });
        box.addEventListener("contextmenu", onContextMenu);
        keys.addEventListener("keydown", onKeyDown);
        keys.addEventListener("keyup", onKeyUp);
        keys.addEventListener("beforeinput", onBeforeInput);
        keys.addEventListener("compositionend", onCompositionEnd);
        keys.addEventListener("paste", onPaste);
        keys.addEventListener("focus", onFocus);
        keys.addEventListener("blur", onBlur);
        return () => {
            if (frame) cancelAnimationFrame(frame);
            picture.style.transform = "";
            box.removeEventListener("pointermove", onPointerMove);
            box.removeEventListener("pointerdown", onPointerDown);
            box.removeEventListener("pointerup", onPointerUp);
            box.removeEventListener("pointercancel", onPointerUp);
            box.removeEventListener("wheel", onWheel);
            box.removeEventListener("contextmenu", onContextMenu);
            keys.removeEventListener("keydown", onKeyDown);
            keys.removeEventListener("keyup", onKeyUp);
            keys.removeEventListener("beforeinput", onBeforeInput);
            keys.removeEventListener("compositionend", onCompositionEnd);
            keys.removeEventListener("paste", onPaste);
            keys.removeEventListener("focus", onFocus);
            keys.removeEventListener("blur", onBlur);
        };
    }, [holder, video, keyboard, session, screen, driving, setTyping]);
}
