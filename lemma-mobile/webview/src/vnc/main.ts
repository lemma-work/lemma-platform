import RFB, { type RawChannel } from "@novnc/novnc";
import {
    base64Of, bytesOf, KEY, KEYBOARD_PAD, needsRefill, specialKeysym, typedKeys,
    type FromPage, type Glass, type ToPage,
} from "../../../src/areas/more/computer/wire";
import "./screen.css";

/** The computer's screen: noVNC, drawing a socket the app holds.
 *
 *  The app opens the VNC socket itself, because only React Native can put
 *  the phone's bearer token in a handshake header, and this page never sees
 *  the token, the address or the server. What it gets is bytes, as base64
 *  (`wire.ts`), handed to noVNC through a channel shaped like a WebSocket;
 *  what noVNC sends goes back the same way. Touch is noVNC's own: a tap
 *  clicks, a drag drags, two fingers scroll.
 *
 *  Typing is this page's, because a phone has no keyboard to capture: a
 *  hidden text box takes what the phone's keyboard types, and what changed in
 *  it is pressed as keys, as noVNC's own page does. The Keyboard button is
 *  here rather than in the app because only a tap inside the page may open
 *  the phone's keyboard on a text box in it. */

declare global {
    interface Window {
        ReactNativeWebView?: { postMessage: (data: string) => void };
        __vnc?: (message: ToPage) => void;
    }
}

const say = (message: FromPage) => window.ReactNativeWebView?.postMessage(JSON.stringify(message));

const CONNECTING = 0;
const OPEN = 1;
const CLOSING = 2;
const CLOSED = 3;

/** A WebSocket as far as noVNC can tell: `core/websock.js` `attach` checks
 *  for these names, takes over the `on*` handlers, and calls `send` with a
 *  view of its own send buffer, which it reuses at once, so it is encoded
 *  here before `send` returns. */
class Bridge implements RawChannel {
    binaryType = "arraybuffer";
    protocol = "";
    readyState: number = CONNECTING;
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: ArrayBuffer | Uint8Array }) => void) | null = null;
    onclose: ((event: { code: number; reason: string; wasClean: boolean }) => void) | null = null;
    onerror: ((event: unknown) => void) | null = null;

    send = (data: Uint8Array | ArrayBuffer) => {
        if (this.readyState !== OPEN) return;
        say({ type: "send", data: base64Of(data instanceof Uint8Array ? data : new Uint8Array(data)) });
    };

    close = () => {
        if (this.readyState === CLOSING || this.readyState === CLOSED) return;
        this.readyState = CLOSING;
        say({ type: "close" });
    };
}

const root = document.getElementById("root")!;
const screen = element("div", { id: "screen" });
const bar = element("div", { id: "bar", role: "toolbar" });
bar.hidden = true;
const keyboardButton = element("button", { type: "button", "aria-pressed": "false" }, "Keyboard");
const pasteButton = element("button", { type: "button" }, "Paste");
const doneButton = element("button", { type: "button" }, "Done");
bar.append(keyboardButton, pasteButton, element("span", { class: "grow" }), doneButton);
const typing = element("textarea", {
    id: "typing",
    "aria-label": "Type on the computer",
    autocapitalize: "off",
    autocomplete: "off",
    autocorrect: "off",
    spellcheck: "false",
    enterkeyhint: "enter",
    tabindex: "-1",
});
root.append(screen, bar, typing);

function element<K extends keyof HTMLElementTagNameMap>(tag: K, attributes: Record<string, string>, text?: string): HTMLElementTagNameMap[K] {
    const made = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) made.setAttribute(name, value);
    if (text) made.textContent = text;
    return made;
}

let rfb: RFB | null = null;
let bridge: Bridge | null = null;

/** A fresh noVNC on a fresh channel, waiting for the app's `open`. The last
 *  picture stays under it until this one connects. */
function attach(viewOnly: boolean) {
    detach();
    const surface = element("div", { class: "surface" });
    screen.append(surface);
    const channel = new Bridge();
    let client: RFB;
    try {
        client = new RFB(surface, channel, { shared: true });
    } catch (failure) {
        surface.remove();
        say({ type: "problem", message: failure instanceof Error ? failure.message : "noVNC would not start." });
        return;
    }
    client.viewOnly = viewOnly;
    client.scaleViewport = true;
    client.background = "transparent";
    /* Text on a web page is what people read here, and noVNC's default JPEG
       quality (6) smears it: highest quality, light compression. */
    client.qualityLevel = 9;
    client.compressionLevel = 2;
    client.focusOnClick = !typingOpen();
    client.addEventListener("connect", () => {
        if (rfb !== client) return;
        for (const stale of Array.from(screen.children)) if (stale !== surface) stale.remove();
        say({ type: "connected" });
    });
    client.addEventListener("disconnect", (event) => {
        if (rfb !== client) return;
        const clean = Boolean((event as CustomEvent<{ clean?: boolean }>).detail?.clean);
        if (screen.children.length > 1) surface.remove();
        say({ type: "disconnected", clean });
    });
    /* The far side copied something: the app puts it on the phone's
       clipboard, since this page may not. */
    client.addEventListener("clipboard", (event) => {
        const text = (event as CustomEvent<{ text?: string }>).detail?.text;
        if (text) say({ type: "clipboard", text });
    });
    client.addEventListener("securityfailure", (event) => {
        const reason = (event as CustomEvent<{ reason?: string }>).detail?.reason;
        say({ type: "problem", message: reason || "The screen refused the connection." });
    });
    rfb = client;
    bridge = channel;
}

/** Let go of the connection. The app closes the socket itself. */
function detach() {
    const client = rfb;
    const channel = bridge;
    rfb = null;
    bridge = null;
    if (!client || !channel) return;
    const wasOpen = channel.readyState !== CLOSED;
    channel.readyState = CLOSED;
    try {
        client.disconnect();
        /* Told straight away, rather than left to time out waiting for a
           close the app is making on its own side. */
        if (wasOpen) channel.onclose?.({ code: 1000, reason: "", wasClean: true });
    } catch {
        /* Already gone. */
    }
}

function press(keys: readonly number[]) {
    if (!rfb) return;
    for (const keysym of keys) {
        rfb.sendKey(keysym, null, true);
        rfb.sendKey(keysym, null, false);
    }
}

/** Ctrl+V as real key events, once the far clipboard holds the text:
 *  `clipboardPasteFrom` only sets it (the web's `sendCtrlV`). */
function paste(text: string) {
    if (!rfb || !text) return;
    rfb.clipboardPasteFrom(text);
    rfb.sendKey(KEY.controlLeft, "ControlLeft", true);
    rfb.sendKey(KEY.lowerV, "KeyV", true);
    rfb.sendKey(KEY.lowerV, "KeyV", false);
    rfb.sendKey(KEY.controlLeft, "ControlLeft", false);
}

/* ── typing ──────────────────────────────────────────────────────────── */

let last = KEYBOARD_PAD;

function refill() {
    typing.value = KEYBOARD_PAD;
    last = KEYBOARD_PAD;
    typing.setSelectionRange(KEYBOARD_PAD.length, KEYBOARD_PAD.length);
}
refill();

function typingOpen(): boolean {
    return document.activeElement === typing;
}

function showTyping(open: boolean) {
    keyboardButton.setAttribute("aria-pressed", open ? "true" : "false");
    if (rfb) rfb.focusOnClick = !open;
}

typing.addEventListener("input", () => {
    const now = typing.value;
    press(typedKeys(last, now));
    if (!needsRefill(now)) {
        last = now;
        return;
    }
    const emptied = Array.from(now).length < 1;
    refill();
    /* An Android keyboard only sees the refill once the box has been let go
       and taken again (noVNC's own page does the same). */
    if (emptied) {
        typing.blur();
        setTimeout(() => typing.focus({ preventScroll: true }), 0);
    }
});

/* A hardware keyboard's keys that type no character. Enter and Backspace
   come through as typing, so they are left to the box. */
typing.addEventListener("keydown", (event) => {
    const keysym = specialKeysym(event.key);
    if (keysym === null || event.isComposing) return;
    event.preventDefault();
    press([keysym]);
});

typing.addEventListener("focus", () => showTyping(true));
typing.addEventListener("blur", () => showTyping(false));

/* While the phone's keyboard is up, a tap on the picture still clicks, but
   must not take focus from the box, or the keyboard would close under the
   person's thumb (noVNC's `keepVirtualKeyboard`). */
const keep = (event: Event) => {
    if (typingOpen()) event.preventDefault();
};
screen.addEventListener("touchstart", keep, { capture: true, passive: false });
screen.addEventListener("mousedown", keep, { capture: true });

/* Whether the box had the keyboard when the button was touched: the touch
   itself moves focus, so asking at `click` is too late. */
let wasTyping = false;
keyboardButton.addEventListener("pointerdown", () => {
    wasTyping = typingOpen();
});
keyboardButton.addEventListener("click", () => {
    if (wasTyping) {
        typing.blur();
    } else {
        refill();
        typing.focus({ preventScroll: true });
    }
});
pasteButton.addEventListener("click", () => say({ type: "paste-request" }));
doneButton.addEventListener("click", () => {
    typing.blur();
    say({ type: "done" });
});

/* ── the app ─────────────────────────────────────────────────────────── */

function dress(glass: Glass) {
    const style = document.documentElement.style;
    style.setProperty("--ground", glass.ground);
    style.setProperty("--ink", glass.ink);
    style.setProperty("--quiet", glass.quiet);
    style.setProperty("--line", glass.line);
    style.setProperty("--press", glass.press);
    bar.style.fontFamily = glass.font;
}

window.__vnc = (message: ToPage) => {
    switch (message.type) {
        case "attach":
            attach(message.viewOnly);
            return;
        case "open":
            if (!bridge) return;
            bridge.readyState = OPEN;
            bridge.protocol = message.protocol;
            bridge.onopen?.();
            return;
        case "frames": {
            const channel = bridge;
            if (!channel || channel.readyState !== OPEN) return;
            for (const data of message.data) {
                const bytes = bytesOf(data);
                channel.onmessage?.({ data: bytes.byteLength === bytes.buffer.byteLength ? bytes.buffer as ArrayBuffer : bytes });
                /* noVNC may have closed it, or been replaced, mid-batch. */
                if (bridge !== channel || channel.readyState !== OPEN) return;
            }
            return;
        }
        case "closed": {
            const channel = bridge;
            if (!channel || channel.readyState === CLOSED) return;
            channel.readyState = CLOSED;
            channel.onclose?.({ code: message.code, reason: message.reason, wasClean: message.code === 1000 });
            return;
        }
        case "keys":
            press(message.keys);
            return;
        case "paste":
            paste(message.text);
            return;
        case "detach":
            typing.blur();
            detach();
            return;
        case "chrome":
            dress(message.glass);
            bar.hidden = message.bar === null;
            doneButton.hidden = !message.bar?.done;
            if (message.bar === null) typing.blur();
            return;
    }
};

say({ type: "ready" });
