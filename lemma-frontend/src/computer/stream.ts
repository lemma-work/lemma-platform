/** The WebRTC screen's rules, apart from the canvas they serve.
 *
 *  Which transport a pane uses, and how a person's keys, pointer and wheel
 *  become the streamer's input messages. Split from the component for the
 *  reason `live.ts` is: a rule can be tested and a video element cannot.
 *
 *  The messages are the streamer's whole input vocabulary
 *  (`sandbox-images/streamer`, `internal/input`): pointer, button, wheel, key,
 *  text, paste and a resize request. Strict there — an unknown field drops the
 *  message — so these builders produce exactly those shapes and nothing else.
 */

export type StreamSetting = "vnc" | "webrtc" | "auto";
export type Transport = "vnc" | "webrtc";

/** Why a pane that was meant to use WebRTC is on VNC. Reported in the beacon;
 *  the same set the API accepts there. */
export type FallbackReason = "ice-timeout" | "ice-failed" | "no-streamer" | "no-turn" | "unsupported" | "error";

/** How long ICE has to connect before the pane gives up and uses VNC. */
export const ICE_DEADLINE_MS = 5_000;

export interface TransportChoice {
    first: Transport;
    /** Whether a failed WebRTC attempt should quietly become VNC. */
    fallback: boolean;
    /** Set when WebRTC was wanted and is not possible here at all. */
    reason?: FallbackReason;
}

/** What this pane uses, from what the server says and what this browser can do.
 *
 *  `?stream=vnc|webrtc` on the page overrides the server for one load, which
 *  is how the team and the bench compare the two on the same machine.
 */
export function chooseTransport(server: StreamSetting | null | undefined, options: {
    rtcSupported: boolean;
    override?: string | null;
}): TransportChoice {
    const wanted = options.override === "vnc" || options.override === "webrtc" ? options.override : server ?? "vnc";
    if (wanted === "vnc") return { first: "vnc", fallback: false };
    if (!options.rtcSupported) return { first: "vnc", fallback: false, reason: "unsupported" };
    return { first: "webrtc", fallback: wanted === "auto" };
}

/** Whether this browser can hold a WebRTC connection at all.
 *
 *  Not just "is the constructor there": the desktop app's WKWebView has shipped
 *  without it, and a page can have the name and still throw on construction.
 *  So one is made and closed. Desktop stays on VNC wherever this says no.
 */
export function rtcSupported(scope: { RTCPeerConnection?: unknown } = globalThis): boolean {
    const Peer = scope.RTCPeerConnection as (new () => { close(): void }) | undefined;
    if (typeof Peer !== "function") return false;
    try {
        new Peer().close();
        return true;
    } catch {
        return false;
    }
}

/* --- input messages ---------------------------------------------------- */

export type InputMessage =
    | { t: "m"; x: number; y: number }
    | { t: "b"; b: 1 | 2 | 3; d: boolean }
    | { t: "w"; dx: number; dy: number }
    | { t: "k"; k: number; d: boolean }
    | { t: "txt"; s: string }
    | { t: "paste"; s: string }
    | { t: "size"; w: number; h: number };

export interface Box { left: number; top: number; width: number; height: number }

/** Where a point on the pane falls on the machine's screen, or null outside it.
 *
 *  The video is drawn `object-fit: contain`, so a pane of a different shape
 *  letterboxes it; a click in the bars is not a click on the page. The
 *  screen's own size is in device pixels, which is what the streamer
 *  validates against, whatever the density.
 */
export function toScreen(clientX: number, clientY: number, box: Box, screen: { width: number; height: number }): { x: number; y: number } | null {
    if (screen.width <= 0 || screen.height <= 0 || box.width <= 0 || box.height <= 0) return null;
    const scale = Math.min(box.width / screen.width, box.height / screen.height);
    const shownWidth = screen.width * scale;
    const shownHeight = screen.height * scale;
    const x = (clientX - box.left - (box.width - shownWidth) / 2) / scale;
    const y = (clientY - box.top - (box.height - shownHeight) / 2) / scale;
    if (x < 0 || y < 0 || x >= screen.width || y >= screen.height) return null;
    return { x: Math.floor(x), y: Math.floor(y) };
}

/** A DOM mouse button as X's: left, middle, right. Others are not sent. */
export function buttonFor(button: number): 1 | 2 | 3 | null {
    if (button === 0) return 1;
    if (button === 1) return 2;
    if (button === 2) return 3;
    return null;
}

/** How many pixels of trackpad scrolling make one wheel notch. A notch is
 *  what X understands, and Chrome scrolls ~100 px for one; a trackpad sends a
 *  stream of small deltas that would each round to nothing. */
const PIXELS_PER_NOTCH = 50;

/** Turns a stream of wheel deltas into whole notches, carrying the remainder,
 *  so a slow trackpad scroll still scrolls and a fast one is not lost. */
export class WheelSteps {
    private x = 0;
    private y = 0;

    /** `deltaMode` 0 is pixels, 1 lines, 2 pages, as on a `WheelEvent`. */
    add(deltaX: number, deltaY: number, deltaMode = 0): { t: "w"; dx: number; dy: number } | null {
        const unit = deltaMode === 1 ? PIXELS_PER_NOTCH : deltaMode === 2 ? PIXELS_PER_NOTCH * 10 : 1;
        this.x += deltaX * unit;
        this.y += deltaY * unit;
        const dx = clampNotches(Math.trunc(this.x / PIXELS_PER_NOTCH));
        const dy = clampNotches(Math.trunc(this.y / PIXELS_PER_NOTCH));
        if (dx === 0 && dy === 0) return null;
        this.x -= dx * PIXELS_PER_NOTCH;
        this.y -= dy * PIXELS_PER_NOTCH;
        return { t: "w", dx, dy };
    }
}

function clampNotches(value: number): number {
    return Math.max(-10, Math.min(10, value));
}

/** X keysyms for the keys that are not characters. */
const NAMED_KEYSYMS: Record<string, number> = {
    Backspace: 0xff08, Tab: 0xff09, Enter: 0xff0d, Escape: 0xff1b, Delete: 0xffff,
    Home: 0xff50, ArrowLeft: 0xff51, ArrowUp: 0xff52, ArrowRight: 0xff53, ArrowDown: 0xff54,
    PageUp: 0xff55, PageDown: 0xff56, End: 0xff57, Insert: 0xff63, ContextMenu: 0xff67,
    NumLock: 0xff7f, CapsLock: 0xffe5, ScrollLock: 0xff14, Pause: 0xff13, PrintScreen: 0xff61,
    F1: 0xffbe, F2: 0xffbf, F3: 0xffc0, F4: 0xffc1, F5: 0xffc2, F6: 0xffc3,
    F7: 0xffc4, F8: 0xffc5, F9: 0xffc6, F10: 0xffc7, F11: 0xffc8, F12: 0xffc9,
};

/** Modifiers by side, since X tells left from right. */
const MODIFIERS: Record<string, [number, number]> = {
    Shift: [0xffe1, 0xffe2], Control: [0xffe3, 0xffe4], Alt: [0xffe9, 0xffea], Meta: [0xffe7, 0xffe8],
};

export interface KeyLike { key: string; code: string; location?: number }

/** The keysym for a key event, or null when it is not one to send.
 *
 *  By `key`, not `code`: `key` is what the person's own layout produced, so a
 *  German or French keyboard types what it says on its keys. The streamer
 *  maps a keysym its keymap lacks onto a spare keycode, so any character
 *  arrives. On a Mac, Command is sent as Control: the machine is Linux, and
 *  Cmd+C there means nothing while Ctrl+C means copy.
 *
 *  Dead keys and IME composition are null here; the composed text is sent as
 *  `txt` when it is done, never as the keystrokes that built it.
 */
export function keysymFor(event: KeyLike, options: { mac: boolean }): number | null {
    const side = event.location === 2 ? 1 : 0;
    if (event.key in MODIFIERS) {
        const pair = event.key === "Meta" && options.mac ? MODIFIERS.Control! : MODIFIERS[event.key]!;
        return pair[side]!;
    }
    if (event.key in NAMED_KEYSYMS) return NAMED_KEYSYMS[event.key]!;
    if (event.key === "Dead" || event.key === "Process" || event.key === "Unidentified") return null;
    const points = Array.from(event.key);
    if (points.length !== 1) return null;
    const point = points[0]!.codePointAt(0)!;
    if (point < 0x20 || point === 0x7f) return null;
    if (point <= 0xff) return point;
    return 0x01000000 + point;
}

/** Whether a key goes to the machine rather than to this page. Browser
 *  shortcuts that leave the page — a new tab, closing this one — stay the
 *  browser's; everything else, including Ctrl/Cmd+L, R and F, is the
 *  machine's, because that is the browser the person is driving. */
export function keyBelongsToMachine(event: { key: string; metaKey: boolean; ctrlKey: boolean }, options: { mac: boolean }): boolean {
    const command = options.mac ? event.metaKey : event.ctrlKey;
    if (!command) return true;
    return !["t", "n", "w", "q", "Tab"].includes(event.key.length === 1 ? event.key.toLowerCase() : event.key);
}

/** Text in the pieces the streamer accepts: at most 256 code points each. */
export function textPieces(text: string, limit = 256): string[] {
    const points = Array.from(text.replace(/\r\n?/g, "\n")).filter((point) => {
        const code = point.codePointAt(0)!;
        return code >= 0x20 || point === "\n" || point === "\t";
    });
    const pieces: string[] = [];
    for (let start = 0; start < points.length; start += limit) pieces.push(points.slice(start, start + limit).join(""));
    return pieces;
}

/** The largest paste the streamer takes, in UTF-8 bytes. */
export const MAX_PASTE_BYTES = 1024 * 1024;

export function pasteMessage(text: string): InputMessage | null {
    if (!text || new TextEncoder().encode(text).length > MAX_PASTE_BYTES) return null;
    return { t: "paste", s: text };
}

export function isMac(platform: string | undefined = globalThis.navigator?.platform): boolean {
    return /Mac|iPhone|iPad|iPod/.test(platform ?? "");
}
