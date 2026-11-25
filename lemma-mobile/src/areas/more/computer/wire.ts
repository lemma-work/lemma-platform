/** What passes between the app and the screen's page (`webview/src/vnc`).
 *
 *  React Native holds the VNC socket, because only it can put the phone's
 *  bearer token in a handshake header, and the page holds noVNC, because only
 *  a browser can draw it. Between them are bytes, carried as base64 strings
 *  both ways, and a few words about the socket's life. The page also turns
 *  what the phone's keyboard types into key presses (`typedKeys`).
 *
 *  Pure, and imported by both sides: the page is built from this file
 *  (`npm run build -- vnc`, so a change here needs that build too), and the
 *  tests hold it. */

/** Bytes per base64 piece: a multiple of three, so every piece but the last
 *  encodes without padding and the pieces join into one string; small
 *  enough that `String.fromCharCode` never takes too many arguments. */
const PIECE = 3 * 4096;

/** Bytes as base64, a piece at a time. `btoa` takes a string of byte-sized
 *  characters, which is what the pieces are made into. */
export function base64Of(bytes: Uint8Array): string {
    let out = "";
    for (let at = 0; at < bytes.length; at += PIECE) {
        const piece = bytes.subarray(at, Math.min(at + PIECE, bytes.length));
        out += btoa(String.fromCharCode.apply(null, piece as unknown as number[]));
    }
    return out;
}

/** Base64 characters per piece when decoding: whole groups of four. */
const GROUPS = 4 * 4096;

/** Base64 as bytes, a piece at a time. Both sides only ever decode what
 *  `base64Of` made, so there is no whitespace to strip. */
export function bytesOf(base64: string): Uint8Array {
    const out = new Uint8Array(Math.floor((base64.length * 3) / 4));
    let written = 0;
    for (let at = 0; at < base64.length; at += GROUPS) {
        const binary = atob(base64.slice(at, at + GROUPS));
        for (let index = 0; index < binary.length; index++) out[written++] = binary.charCodeAt(index);
    }
    return written === out.length ? out : out.subarray(0, written);
}

/** X11 keysyms for the keys a phone's keyboard cannot type as a character. */
export const KEY = {
    backspace: 0xff08,
    tab: 0xff09,
    enter: 0xff0d,
    escape: 0xff1b,
    left: 0xff51,
    up: 0xff52,
    right: 0xff53,
    down: 0xff54,
    home: 0xff50,
    end: 0xff57,
    delete: 0xffff,
    controlLeft: 0xffe3,
    lowerV: 0x76,
} as const;

/** The keysym for a key a hardware keyboard sends that types no character
 *  (by `KeyboardEvent.key`), or null for one that does, or that the text box
 *  already reports as typing. */
export function specialKeysym(key: string): number | null {
    switch (key) {
        case "Tab": return KEY.tab;
        case "Escape": return KEY.escape;
        case "ArrowLeft": return KEY.left;
        case "ArrowUp": return KEY.up;
        case "ArrowRight": return KEY.right;
        case "ArrowDown": return KEY.down;
        case "Home": return KEY.home;
        case "End": return KEY.end;
        case "Delete": return KEY.delete;
        default: return null;
    }
}

/** The keysym that types one character.
 *
 *  Latin-1 is its own keysym; any other character is `0x01000000` plus its
 *  code point, which X servers take as "this Unicode character". A newline is
 *  Enter and a tab is Tab; other control characters type nothing. */
export function keysymOf(codePoint: number): number | null {
    if (codePoint === 0x0a || codePoint === 0x0d) return KEY.enter;
    if (codePoint === 0x09) return KEY.tab;
    if (codePoint === 0x08) return KEY.backspace;
    if ((codePoint >= 0x20 && codePoint <= 0x7e) || (codePoint >= 0xa0 && codePoint <= 0xff)) return codePoint;
    if (codePoint < 0x20 || (codePoint >= 0x7f && codePoint < 0xa0)) return null;
    if (codePoint > 0x10ffff) return null;
    return 0x01000000 + codePoint;
}

/** What changed in a text box, as key presses: noVNC's own reading of its
 *  hidden input (`keyInput` in its `app/ui.js`). Everything after the first
 *  character that differs was rubbed out and typed again, which is also how
 *  an autocorrection reaches a machine that only knows keys. By code point,
 *  so a character outside the Basic Multilingual Plane is one key, not two. */
export function typedKeys(before: string, after: string): number[] {
    const was = Array.from(before);
    const now = Array.from(after);
    let same = 0;
    while (same < was.length && same < now.length && was[same] === now[same]) same++;
    const keys: number[] = [];
    for (let rubbed = same; rubbed < was.length; rubbed++) keys.push(KEY.backspace);
    for (const character of now.slice(same)) {
        const keysym = keysymOf(character.codePointAt(0) ?? 0);
        if (keysym !== null) keys.push(keysym);
    }
    return keys;
}

/** What the hidden text box is kept filled with, so Backspace always has a
 *  character to take: a phone's keyboard sends nothing for a Backspace in an
 *  empty box. */
export const KEYBOARD_PAD = "_".repeat(40);

/** Whether the hidden box has drifted far enough to fill it again. */
export function needsRefill(value: string): boolean {
    const length = Array.from(value).length;
    return length < 1 || length > KEYBOARD_PAD.length * 2;
}

/** What the app tells the page. */
export type ToPage =
    /** A new connection is starting: a fresh noVNC on a fresh channel,
     *  waiting for `open`. */
    | { type: "attach"; viewOnly: boolean }
    /** The socket opened. */
    | { type: "open"; protocol: string }
    /** What the socket received, in order, as base64. */
    | { type: "frames"; data: string[] }
    /** The socket closed, with its code. */
    | { type: "closed"; code: number; reason: string }
    /** Press and release each of these keysyms. */
    | { type: "keys"; keys: number[] }
    /** Put this on the far clipboard, then press Ctrl+V. */
    | { type: "paste"; text: string }
    /** Drop the connection: nobody is looking. */
    | { type: "detach" }
    /** The bar under the picture, and the glass it sits on. Null hides it. */
    | { type: "chrome"; bar: { done: boolean } | null; glass: Glass };

/** The glass the picture sits on, and what is drawn on it: the dark
 *  palette's tokens, since a screen is dark in every theme. */
export interface Glass {
    ground: string;
    ink: string;
    quiet: string;
    line: string;
    press: string;
    font: string;
}

/** What the page tells the app. */
export type FromPage =
    | { type: "ready" }
    /** Bytes noVNC wants sent, as base64. */
    | { type: "send"; data: string }
    /** noVNC closed its channel. */
    | { type: "close" }
    /** The VNC handshake finished: there is a picture. */
    | { type: "connected" }
    | { type: "disconnected"; clean: boolean }
    /** Something was copied on the far side. */
    | { type: "clipboard"; text: string }
    /** Paste was pressed: the page cannot read the phone's clipboard. */
    | { type: "paste-request" }
    /** Done was pressed: leave full screen. */
    | { type: "done" }
    | { type: "problem"; message: string };

/** One message from the page, or null for anything else. */
export function fromPage(raw: string): FromPage | null {
    let said: unknown;
    try {
        said = JSON.parse(raw);
    } catch {
        return null;
    }
    if (!said || typeof said !== "object") return null;
    const message = said as Record<string, unknown>;
    switch (message.type) {
        case "ready":
        case "close":
        case "connected":
        case "paste-request":
        case "done":
            return { type: message.type };
        case "send":
            return typeof message.data === "string" ? { type: "send", data: message.data } : null;
        case "disconnected":
            return { type: "disconnected", clean: message.clean === true };
        case "clipboard":
            return typeof message.text === "string" ? { type: "clipboard", text: message.text } : null;
        case "problem":
            return { type: "problem", message: typeof message.message === "string" ? message.message : "" };
        default:
            return null;
    }
}

/** The script that hands the page one message. The page answers to
 *  `window.__vnc`; the trailing `true` is what `injectJavaScript` wants. */
export function toPageScript(message: ToPage): string {
    return `window.__vnc&&window.__vnc(${JSON.stringify(message)});true;`;
}
