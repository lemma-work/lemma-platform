import test, { mock } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
    WheelSteps, buttonFor, chooseTransport, keyBelongsToMachine, keysymFor, pasteMessage, rtcSupported,
    textPieces, toScreen, MAX_PASTE_BYTES,
} from "../src/computer/stream.ts";
import { PRESETS, presetForSize, presetNamed } from "../src/computer/presets.ts";
import { SessionReport } from "../src/computer/beacon.ts";
import { CLOSE_USE_VNC, RtcSession, type RtcHandlers } from "../src/computer/rtc-session.ts";
import { socketUrl } from "../src/computer/live.ts";

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
        onClosed: (code) => log.push(`closed:${code}`),
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

void mock;
