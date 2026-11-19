import test, { mock, type TestContext } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
    WheelSteps, buttonFor, chooseTransport, keyBelongsToMachine, keysymFor, pasteMessage, rtcSupported,
    textPieces, toScreen, MAX_PASTE_BYTES, COPY_WINDOW_MS, MachineClipboard, RTC_FAILURES_BEFORE_VNC, afterRtcClose,
    connectMode, drivesAtFirst, isCopyChord, peerStateAction, rtcFailureGoesToVnc,
} from "../src/computer/stream.ts";
import { PRESETS, presetForSize, presetNamed } from "../src/computer/presets.ts";
import { SessionReport } from "../src/computer/beacon.ts";
import { CLOSE_USE_VNC, RtcSession, type RtcHandlers } from "../src/computer/rtc-session.ts";
import { HIDDEN_DISCONNECT_MS, socketUrl, watchHidden } from "../src/computer/live.ts";

const ROOT = path.resolve(import.meta.dirname, "..");
const source = (file: string) => readFileSync(path.join(ROOT, file), "utf8");

/* --- which transport --------------------------------------------------- */

test("VNC unless the server rolled WebRTC out to this person", () => {
    assert.deepEqual(chooseTransport(undefined, { rtcSupported: true }), { first: "vnc", fallback: false });
    assert.deepEqual(chooseTransport("vnc", { rtcSupported: true }), { first: "vnc", fallback: false });
    assert.deepEqual(chooseTransport("auto", { rtcSupported: true }), { first: "webrtc", fallback: true });
    assert.deepEqual(chooseTransport("webrtc", { rtcSupported: true }), { first: "webrtc", fallback: false });
});

test("a browser without WebRTC stays on VNC and says why", () => {
    assert.deepEqual(chooseTransport("auto", { rtcSupported: false }), { first: "vnc", fallback: false, reason: "unsupported" });
});

test("?stream= overrides the server for one load", () => {
    assert.equal(chooseTransport("vnc", { rtcSupported: true, override: "webrtc" }).first, "webrtc");
    assert.equal(chooseTransport("auto", { rtcSupported: true, override: "vnc" }).first, "vnc");
    assert.equal(chooseTransport("auto", { rtcSupported: true, override: "nonsense" }).first, "webrtc");
});

test("the desktop app's WebView is probed, not trusted by name", () => {
    assert.equal(rtcSupported({}), false);
    assert.equal(rtcSupported({ RTCPeerConnection: "not a constructor" }), false);
    class Throws { constructor() { throw new Error("not in this WebView"); } close() {} }
    assert.equal(rtcSupported({ RTCPeerConnection: Throws }), false);
    let closed = false;
    class Works { close() { closed = true; } }
    assert.equal(rtcSupported({ RTCPeerConnection: Works }), true);
    assert.equal(closed, true);
});

test("the socket names the transport and the pane", () => {
    const url = new URL(socketUrl("https://api.example.test", { mode: "view", transport: "rtc", viewer: "abc123" }));
    assert.equal(url.pathname, "/machines/me/browser/view");
    assert.equal(url.searchParams.get("transport"), "rtc");
    assert.equal(url.searchParams.get("viewer"), "abc123");
    const vnc = new URL(socketUrl("https://api.example.test", { mode: "view" }));
    assert.equal(vnc.searchParams.get("transport"), null);
});

/* --- pointer, wheel ----------------------------------------------------- */

test("a click lands where it was aimed on a letterboxed picture", () => {
    /* A 1280x800 screen in a 1000x1000 pane: 1000x625 shown, 187.5px bars. */
    const box = { left: 10, top: 20, width: 1000, height: 1000 };
    const screen = { width: 1280, height: 800 };
    assert.deepEqual(toScreen(10, 20 + 187.5, box, screen), { x: 0, y: 0 });
    assert.deepEqual(toScreen(10 + 500, 20 + 500, box, screen), { x: 640, y: 400 });
    assert.equal(toScreen(510, 100, box, screen), null, "the bar above the picture is not the page");
    assert.equal(toScreen(510, 20 + 1000 - 1, box, screen), null);
});

test("a density-2 screen is addressed in its own pixels", () => {
    const box = { left: 0, top: 0, width: 1280, height: 800 };
    assert.deepEqual(toScreen(640, 400, box, { width: 2560, height: 1600 }), { x: 1280, y: 800 });
});

test("buttons map to X's and nothing else is sent", () => {
    assert.equal(buttonFor(0), 1);
    assert.equal(buttonFor(1), 2);
    assert.equal(buttonFor(2), 3);
    assert.equal(buttonFor(3), null);
    assert.equal(buttonFor(4), null);
});

test("a trackpad's small deltas add up to notches, and the rest is kept", () => {
    const wheel = new WheelSteps();
    assert.equal(wheel.add(0, 20), null);
    assert.equal(wheel.add(0, 20), null);
    assert.deepEqual(wheel.add(0, 20), { t: "w", dx: 0, dy: 1 });
    assert.deepEqual(wheel.add(0, -60), { t: "w", dx: 0, dy: -1 });
    assert.deepEqual(wheel.add(0, 3, 1), { t: "w", dx: 0, dy: 3 }, "lines are notches");
    assert.deepEqual(new WheelSteps().add(10_000, -10_000), { t: "w", dx: 10, dy: -10 }, "clamped to the streamer's range");
});

/* --- keys --------------------------------------------------------------- */

test("characters are sent as the layout typed them", () => {
    const mac = { mac: false };
    assert.equal(keysymFor({ key: "a", code: "KeyA" }, mac), 0x61);
    assert.equal(keysymFor({ key: "A", code: "KeyA" }, mac), 0x41);
    assert.equal(keysymFor({ key: "é", code: "Digit2" }, mac), 0xe9, "Latin-1 is its own keysym");
    assert.equal(keysymFor({ key: "€", code: "KeyE" }, mac), 0x010020ac, "beyond Latin-1, the Unicode range");
    assert.equal(keysymFor({ key: "ж", code: "Semicolon" }, mac), 0x01000436);
});

test("named keys and both sides of each modifier", () => {
    const mac = { mac: false };
    assert.equal(keysymFor({ key: "Enter", code: "Enter" }, mac), 0xff0d);
    assert.equal(keysymFor({ key: "ArrowLeft", code: "ArrowLeft" }, mac), 0xff51);
    assert.equal(keysymFor({ key: "F12", code: "F12" }, mac), 0xffc9);
    assert.equal(keysymFor({ key: "Shift", code: "ShiftLeft", location: 1 }, mac), 0xffe1);
    assert.equal(keysymFor({ key: "Shift", code: "ShiftRight", location: 2 }, mac), 0xffe2);
    assert.equal(keysymFor({ key: "Meta", code: "MetaLeft", location: 1 }, mac), 0xffe7);
});

test("on a Mac, Command drives the machine's Control", () => {
    assert.equal(keysymFor({ key: "Meta", code: "MetaLeft", location: 1 }, { mac: true }), 0xffe3);
    assert.equal(keysymFor({ key: "Meta", code: "MetaRight", location: 2 }, { mac: true }), 0xffe4);
});

test("dead keys and IME keystrokes are not sent; their text is", () => {
    for (const key of ["Dead", "Process", "Unidentified", "\u0007"]) {
        assert.equal(keysymFor({ key, code: "" }, { mac: false }), null);
    }
    assert.deepEqual(textPieces("日本語"), ["日本語"]);
});

test("shortcuts that leave the page stay this browser's", () => {
    const at = (key: string, meta: boolean, ctrl: boolean, mac: boolean) =>
        keyBelongsToMachine({ key, metaKey: meta, ctrlKey: ctrl }, { mac });
    assert.equal(at("t", true, false, true), false, "Cmd+T opens a tab here");
    assert.equal(at("w", false, true, false), false, "Ctrl+W closes this tab");
    assert.equal(at("l", true, false, true), true, "Cmd+L is the machine's address bar");
    assert.equal(at("c", true, false, true), true);
    assert.equal(at("x", false, false, true), true);
});

test("text goes in pieces the streamer accepts, without control characters", () => {
    const long = "a".repeat(600);
    assert.deepEqual(textPieces(long).map((piece) => Array.from(piece).length), [256, 256, 88]);
    assert.deepEqual(textPieces("a\u0000b\r\nc\td"), ["ab\nc\td"]);
    assert.deepEqual(textPieces("😀".repeat(300)).map((piece) => Array.from(piece).length), [256, 44]);
});

test("a paste past the streamer's limit is not sent", () => {
    assert.deepEqual(pasteMessage("hello"), { t: "paste", s: "hello" });
    assert.equal(pasteMessage(""), null);
    assert.equal(pasteMessage("x".repeat(MAX_PASTE_BYTES + 1)), null);
});

/* --- presets ------------------------------------------------------------ */

test("the presets are the ones the relay accepts", () => {
    const relay = source("../lemma-backend/sandbox_runtime/browser_relay/display.py");
    const listed = Array.from(relay.matchAll(/"(\w+)": \((\d+), (\d+)\)/g), ([, name, w, h]) => [name, Number(w), Number(h)]);
    assert.deepEqual(listed, PRESETS.map((preset) => [preset.name, preset.width, preset.height]));
});

test("a size is matched to its preset, and an unknown name is the default", () => {
    assert.equal(presetForSize("820x1180")?.name, "tablet");
    assert.equal(presetForSize("1000x700"), null);
    assert.equal(presetNamed("nonsense").name, "desktop");
});

test("changing the pane's shape never asks the machine to resize", () => {
    /* The whole point of presets: the pane rescales locally. Neither
       transport watches its own box, and the one resize left is keyed on the
       preset, the connection and driving -- never on a size. */
    for (const file of ["src/computer/live-screen.tsx", "src/computer/live-screen-rtc.tsx"]) {
        const code = source(file);
        assert.doesNotMatch(code, /ResizeObserver/, file);
        assert.doesNotMatch(code, /useBrowserResize|display-size/, file);
    }
    const view = source("src/computer/live-view.tsx");
    assert.doesNotMatch(view, /ResizeObserver|getBoundingClientRect/);
    assert.match(view, /\}, \[preset, live, driving, viewer\]\);/);
});

/* --- the beacon --------------------------------------------------------- */

test("a session's report is the median of what it saw", () => {
    const report = new SessionReport("webrtc", 2, 0);
    report.firstFrame(800);
    for (const fps of [30, 29, 31, 12]) {
        report.sample({ fps, kbps: 2500, latencyMs: 60, codec: "h264", relay: "udp" });
    }
    assert.deepEqual(report.beacon(60_000), {
        transport: "webrtc", seconds: 60, density: 2, first_frame_ms: 800,
        fps: 29.5, latency_ms: 60, kbps: 2500, codec: "h264", relay: "udp",
    });
});

test("a fallback is reported, and VNC is not credited with WebRTC's numbers", () => {
    const report = new SessionReport("webrtc", 1, 0);
    report.sample({ fps: 30, kbps: 2000, latencyMs: 50, codec: "vp8", relay: "tls" });
    report.fellBack("ice-timeout");
    assert.deepEqual(report.beacon(10_000), { transport: "vnc", fallback: "ice-timeout", seconds: 10, density: 1 });
});

test("a pane opened and closed at once reports nothing", () => {
    assert.equal(new SessionReport("vnc", 1, 0).beacon(500), null);
});

/* --- the connection: deadline, fallback, events ------------------------- */

class FakeSocket {
    static last: FakeSocket | null = null;
    static OPEN = 1;
    readyState = 1;
    sent: string[] = [];
    private listeners = new Map<string, ((event: unknown) => void)[]>();
    url: string;
    constructor(url: string) { this.url = url; FakeSocket.last = this; }
    addEventListener(type: string, listener: (event: unknown) => void) {
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
    }
    send(data: string) { this.sent.push(data); }
    close() { this.readyState = 3; }
    emit(type: string, event: unknown) { for (const listener of this.listeners.get(type) ?? []) listener(event); }
}

function handlers(log: string[]): RtcHandlers {
    return {
        onStream: () => log.push("stream"),
        onLive: () => log.push("live"),
        onControl: (driving, may) => log.push(`control:${driving}:${may}`),
        onCursor: (shape) => log.push(`cursor:${shape.id}`),
        onPointer: (x, y) => log.push(`pos:${x},${y}`),
        onScreenSize: (w, h) => log.push(`size:${w}x${h}`),
        onClipboard: (text) => log.push(`clip:${text}`),
        onAgentDriving: (driving) => log.push(`agent:${driving}`),
        onFallback: (reason) => log.push(`fallback:${reason}`),
        onClosed: (code, wasLive) => log.push(`closed:${code}${wasLive ? ":live" : ""}`),
        onRevoked: () => log.push("revoked"),
    };
}

/** Just enough of a peer connection for `start` to reach its offer. Node has
 *  none; ICE never connects on this one, which is the case under test. */
class FakePeer {
    localDescription: { sdp: string } | null = null;
    addTransceiver() { return {}; }
    createDataChannel() { return { addEventListener() {}, close() {} }; }
    addEventListener() {}
    async createOffer() { return { type: "offer", sdp: "v=0" }; }
    async setLocalDescription(offer: { sdp: string }) { this.localDescription = offer; }
    close() {}
}

const CONFIG = JSON.stringify({ t: "config", ice_servers: [], policy: "relay", mode: "view", may_control: false });

test("WebRTC that has not connected five seconds after its config becomes VNC", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const original = globalThis.WebSocket;
    const originalPeer = globalThis.RTCPeerConnection;
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    globalThis.RTCPeerConnection = FakePeer as unknown as typeof RTCPeerConnection;
    try {
        const log: string[] = [];
        new RtcSession("wss://api.test/view", handlers(log));
        FakeSocket.last!.emit("message", { data: CONFIG });
        t.mock.timers.tick(4_999);
        assert.deepEqual(log, []);
        t.mock.timers.tick(1);
        assert.deepEqual(log, ["fallback:ice-timeout"]);
    } finally {
        globalThis.WebSocket = original;
        globalThis.RTCPeerConnection = originalPeer;
    }
});

test("a browser still starting does not use up the ICE deadline", (t) => {
    /* Everything before the config is the machine starting its browser, and a
       cold start is longer than the deadline. Timed from the socket opening,
       every cold start fell back to VNC with nothing wrong with the network. */
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const original = globalThis.WebSocket;
    const originalPeer = globalThis.RTCPeerConnection;
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    globalThis.RTCPeerConnection = FakePeer as unknown as typeof RTCPeerConnection;
    try {
        const log: string[] = [];
        new RtcSession("wss://api.test/view", handlers(log));
        t.mock.timers.tick(60_000);
        assert.deepEqual(log, [], "no fallback while the machine has said nothing");
        FakeSocket.last!.emit("message", { data: CONFIG });
        t.mock.timers.tick(4_999);
        assert.deepEqual(log, []);
        t.mock.timers.tick(1);
        assert.deepEqual(log, ["fallback:ice-timeout"]);
    } finally {
        globalThis.WebSocket = original;
        globalThis.RTCPeerConnection = originalPeer;
    }
});

test("an image without a streamer is a quiet switch to VNC", () => {
    const original = globalThis.WebSocket;
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    try {
        const log: string[] = [];
        const session = new RtcSession("wss://api.test/view", handlers(log));
        FakeSocket.last!.emit("close", { code: CLOSE_USE_VNC });
        assert.deepEqual(log, ["fallback:no-streamer"]);
        session.close();
    } finally {
        globalThis.WebSocket = original;
    }
});

test("a refusal before connecting is the pane's to read, not a fallback", () => {
    const original = globalThis.WebSocket;
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    try {
        const log: string[] = [];
        new RtcSession("wss://api.test/view", handlers(log));
        FakeSocket.last!.emit("close", { code: 4401 });
        assert.deepEqual(log, ["closed:4401"]);
    } finally {
        globalThis.WebSocket = original;
    }
});

test("input is not sent by a pane that is only watching", () => {
    const original = globalThis.WebSocket;
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    try {
        const session = new RtcSession("wss://api.test/view", handlers([]));
        assert.equal(session.send({ t: "k", k: 0x61, d: true }), false);
        session.take();
        assert.deepEqual(FakeSocket.last!.sent.map((raw) => JSON.parse(raw)), [{ t: "take" }]);
        session.close();
        assert.deepEqual(JSON.parse(FakeSocket.last!.sent.at(-1)!), { t: "bye" });
    } finally {
        globalThis.WebSocket = original;
    }
});

test("the pane never sends a ticket or a mode", () => {
    const code = source("src/computer/rtc-session.ts");
    const sent = Array.from(code.matchAll(/this\.signal\(\{\s*t: "(\w+)"/g), ([, kind]) => kind);
    assert.deepEqual([...new Set(sent)].sort(), ["bye", "candidate", "offer", "release", "take"]);
    assert.doesNotMatch(code, /ticket\s*:/, "no message this side builds carries a ticket");
});

/* --- watching or driving, across reconnects ----------------------------- */

test("a reconnect asks for what the pane wants now, never for control it gave back", () => {
    assert.equal(connectMode(true), "control");
    assert.equal(connectMode(false), "view");
});

test("your own machine opens driving; a pod's opens watching", () => {
    assert.equal(drivesAtFirst("mine"), true);
    assert.equal(drivesAtFirst("pod"), false);
});

test("the socket's mode is read from the wish at connect time, not fixed at mount", () => {
    const code = source("src/computer/live-screen-rtc.tsx");
    assert.match(code, /mode: connectMode\(wants\.current\)/);
});

/* --- giving up on WebRTC ------------------------------------------------ */

test("connects that keep failing end on VNC instead of retrying forever", () => {
    let failures = 0;
    const actions: string[] = [];
    for (let i = 0; i < RTC_FAILURES_BEFORE_VNC; i++) {
        const next = afterRtcClose({ settled: false, noBrowser: false, wasLive: false, failures });
        failures = next.failures;
        actions.push(next.action);
    }
    assert.deepEqual(actions, ["retry", "retry", "vnc"]);
});

test("a connection that had a picture resets the count; a settled close is never retried", () => {
    assert.deepEqual(afterRtcClose({ settled: false, noBrowser: false, wasLive: true, failures: 2 }), { action: "retry", failures: 0 });
    assert.equal(afterRtcClose({ settled: true, noBrowser: false, wasLive: false, failures: 2 }).action, "settle");
    /* No browser yet is waiting, not failing: VNC would hear the same. */
    assert.deepEqual(afterRtcClose({ settled: false, noBrowser: true, wasLive: false, failures: 2 }), { action: "wait", failures: 0 });
});

test("a missing streamer or a pane that gave up goes to VNC even where WebRTC was asked for alone", () => {
    assert.equal(rtcFailureGoesToVnc("ice-timeout", { fallback: true, gaveUp: false }), true);
    assert.equal(rtcFailureGoesToVnc("ice-timeout", { fallback: false, gaveUp: false }), false);
    assert.equal(rtcFailureGoesToVnc("no-streamer", { fallback: false, gaveUp: false }), true);
    assert.equal(rtcFailureGoesToVnc("error", { fallback: false, gaveUp: true }), true);
});

/* --- a dropped connection is not a live one ---------------------------- */

test("a peer's states, read for the pane", () => {
    assert.equal(peerStateAction("connected"), "live");
    assert.equal(peerStateAction("disconnected"), "grace");
    assert.equal(peerStateAction("failed"), "lost");
    assert.equal(peerStateAction("closed"), "lost");
    assert.equal(peerStateAction("connecting"), "none");
    assert.equal(peerStateAction("new"), "none");
});

/** A peer whose connection state the test drives. */
class LivePeer extends FakePeer {
    static last: LivePeer | null = null;
    connectionState = "new";
    closed = false;
    private changes: (() => void)[] = [];
    constructor() { super(); LivePeer.last = this; }
    override addEventListener(type?: string, listener?: () => void) {
        if (type === "connectionstatechange" && listener) this.changes.push(listener);
    }
    override close() { this.closed = true; }
    become(state: string) { this.connectionState = state; for (const change of this.changes) change(); }
}

async function connectedSession(t: TestContext, log: string[]) {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    globalThis.RTCPeerConnection = LivePeer as unknown as typeof RTCPeerConnection;
    new RtcSession("wss://api.test/view", handlers(log));
    FakeSocket.last!.emit("message", { data: CONFIG });
    await new Promise((resolve) => setImmediate(resolve));
    LivePeer.last!.become("connected");
}

function withGlobals(run: () => Promise<void>) {
    return async () => {
        const original = globalThis.WebSocket;
        const originalPeer = globalThis.RTCPeerConnection;
        try { await run(); } finally {
            globalThis.WebSocket = original;
            globalThis.RTCPeerConnection = originalPeer;
        }
    };
}

test("the signalling socket closing after connecting is a reconnect, not a frozen picture", async (t) => {
    await withGlobals(async () => {
        const log: string[] = [];
        await connectedSession(t, log);
        FakeSocket.last!.emit("close", { code: 1012 });
        assert.deepEqual(log, ["live", "closed:1012:live"]);
        assert.equal(LivePeer.last!.closed, true, "the peer is torn down with it");
        FakeSocket.last!.emit("close", { code: 1006 });
        assert.equal(log.length, 2, "said once");
    })();
});

test("a peer disconnected past the grace is lost; one that recovers is not", async (t) => {
    await withGlobals(async () => {
        const log: string[] = [];
        await connectedSession(t, log);
        LivePeer.last!.become("disconnected");
        t.mock.timers.tick(2_000);
        LivePeer.last!.become("connected");
        t.mock.timers.tick(10_000);
        assert.deepEqual(log, ["live"], "a hiccup ICE recovered from");
        LivePeer.last!.become("disconnected");
        t.mock.timers.tick(2_999);
        assert.deepEqual(log, ["live"]);
        t.mock.timers.tick(1);
        assert.deepEqual(log, ["live", "closed:1006:live"]);
    })();
});

test("a peer that closes after connecting is lost at once", async (t) => {
    await withGlobals(async () => {
        const log: string[] = [];
        await connectedSession(t, log);
        LivePeer.last!.become("closed");
        assert.deepEqual(log, ["live", "closed:1006:live"]);
    })();
});

test("4427 still means VNC after connecting", async (t) => {
    await withGlobals(async () => {
        const log: string[] = [];
        await connectedSession(t, log);
        FakeSocket.last!.emit("close", { code: CLOSE_USE_VNC });
        assert.deepEqual(log, ["live", "fallback:no-streamer"]);
    })();
});

/* --- the machine's clipboard ------------------------------------------- */

test("the machine's clipboard is written here only right after a copy chord in the pane", () => {
    const clip = new MachineClipboard();
    assert.equal(clip.received("set by a page", 1_000), null, "unasked: held, not written");
    assert.equal(clip.latest, "set by a page");
    clip.arm(5_000);
    assert.equal(clip.received("what they copied", 5_000 + COPY_WINDOW_MS), "what they copied");
    assert.equal(clip.received("and then a page", 5_000 + COPY_WINDOW_MS), null, "one write per chord");
    clip.arm(10_000);
    assert.equal(clip.received("too late", 10_001 + COPY_WINDOW_MS), null);
    assert.equal(clip.latest, "too late");
});

test("copy chords: Ctrl+C/X, or Cmd on a Mac", () => {
    const key = (k: string, mods: { ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean } = {}) =>
        ({ key: k, ctrlKey: false, metaKey: false, ...mods });
    assert.equal(isCopyChord(key("c", { ctrlKey: true }), { mac: false }), true);
    assert.equal(isCopyChord(key("X", { ctrlKey: true }), { mac: false }), true);
    assert.equal(isCopyChord(key("c", { metaKey: true }), { mac: false }), false);
    assert.equal(isCopyChord(key("c", { metaKey: true }), { mac: true }), true);
    assert.equal(isCopyChord(key("v", { ctrlKey: true }), { mac: false }), false);
    assert.equal(isCopyChord(key("c"), { mac: false }), false);
    assert.equal(isCopyChord(key("c", { ctrlKey: true, altKey: true }), { mac: false }), false);
});

test("no pane writes the machine's clipboard unguarded", () => {
    for (const file of ["src/computer/live-screen-rtc.tsx", "src/computer/live-screen.tsx"]) {
        const code = source(file);
        for (const [call] of code.matchAll(/copyText\([^)]*\)/g)) assert.equal(call, "copyText(asked)", file);
    }
});

/* --- a hidden page ------------------------------------------------------ */

class FakeDocument {
    hidden = false;
    private listeners: (() => void)[] = [];
    addEventListener(_type: "visibilitychange", listener: () => void) { this.listeners.push(listener); }
    removeEventListener(_type: "visibilitychange", listener: () => void) { this.listeners = this.listeners.filter((l) => l !== listener); }
    set(hidden: boolean) { this.hidden = hidden; for (const listener of this.listeners) listener(); }
}

test("a page hidden for a minute lets its screen go, and gets it back when shown", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const doc = new FakeDocument();
    const said: boolean[] = [];
    const stop = watchHidden(doc, (away) => said.push(away));
    doc.set(true);
    t.mock.timers.tick(HIDDEN_DISCONNECT_MS - 1);
    assert.deepEqual(said, []);
    doc.set(false);
    doc.set(true);
    t.mock.timers.tick(HIDDEN_DISCONNECT_MS - 1);
    assert.deepEqual(said, [], "a look away resets the minute");
    t.mock.timers.tick(1);
    assert.deepEqual(said, [true]);
    doc.set(true);
    t.mock.timers.tick(HIDDEN_DISCONNECT_MS * 2);
    assert.deepEqual(said, [true], "said once");
    doc.set(false);
    assert.deepEqual(said, [true, false]);
    stop();
    doc.set(true);
    t.mock.timers.tick(HIDDEN_DISCONNECT_MS);
    assert.deepEqual(said, [true, false], "nothing after cleanup");
});

void mock;


test("a connect the API never answered is retried and not counted toward VNC", () => {
    let failures = 0;
    for (let i = 0; i < RTC_FAILURES_BEFORE_VNC * 3; i += 1) {
        const next = afterRtcClose({ settled: false, noBrowser: false, wasLive: false, reached: false, failures });
        assert.equal(next.action, "retry");
        failures = next.failures;
    }
    assert.equal(failures, 0);
    /* Once it answers and WebRTC still fails, those count as before. */
    const answered = afterRtcClose({ settled: false, noBrowser: false, wasLive: false, reached: true, failures });
    assert.deepEqual(answered, { action: "retry", failures: 1 });
});

test("a close says whether the API accepted the socket first", () => {
    const original = globalThis.WebSocket;
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    try {
        const seen: boolean[] = [];
        const record: RtcHandlers = { ...handlers([]), onClosed: (_code, _live, reached) => seen.push(reached) };
        /* The API is away: the socket never opens. */
        new RtcSession("wss://api.test/view", record);
        FakeSocket.last!.emit("close", { code: 1006 });
        /* The API answered, then closed it: the streamer could not start. */
        new RtcSession("wss://api.test/view", record);
        FakeSocket.last!.emit("open", {});
        FakeSocket.last!.emit("close", { code: 1011 });
        assert.deepEqual(seen, [false, true]);
    } finally {
        globalThis.WebSocket = original;
    }
});
