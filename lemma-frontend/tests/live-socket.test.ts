import test from "node:test";
import assert from "node:assert/strict";
import { LiveCallError, LiveSocket, liveUrl, reconnectDelay, type LiveClock, type LiveHandlers, type WebSocketLike } from "../src/live/socket.ts";

/** The pod's live socket, driven frame by frame: a fake WebSocket the test
 *  answers as the server would, and a clock the test moves by hand. */

class FakeSocket implements WebSocketLike {
    sent: Record<string, unknown>[] = [];
    closed: number | null = null;
    onmessage: ((event: { data: unknown }) => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    onerror: ((event: unknown) => void) | null = null;
    readonly url: string;
    constructor(url: string) { this.url = url; }
    send(text: string) { this.sent.push(JSON.parse(text)); }
    close(code = 1000) { this.closed = code; }
    /** The server says something. */
    hear(frame: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(frame) }); }
    /** The server, or the network, drops it. */
    drop(code: number) { this.onclose?.({ code }); }
    subscribes() { return this.sent.filter((frame) => frame.op === "subscribe"); }
}

class FakeClock implements LiveClock {
    now = 0;
    private next = 1;
    private timers = new Map<number, { at: number; run: () => void; every?: number }>();
    setTimeout(run: () => void, ms: number) { const id = this.next++; this.timers.set(id, { at: this.now + ms, run }); return id; }
    clearTimeout(timer: unknown) { this.timers.delete(timer as number); }
    setInterval(run: () => void, ms: number) { const id = this.next++; this.timers.set(id, { at: this.now + ms, run, every: ms }); return id; }
    clearInterval(timer: unknown) { this.timers.delete(timer as number); }
    advance(ms: number) {
        const until = this.now + ms;
        for (;;) {
            const due = [...this.timers.entries()].filter(([, timer]) => timer.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            const [id, timer] = due;
            this.now = timer.at;
            if (timer.every) timer.at += timer.every; else this.timers.delete(id);
            timer.run();
        }
        this.now = until;
    }
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function harness(options: { token?: string | null; refresh?: () => Promise<void> } = {}) {
    const sockets: FakeSocket[] = [];
    const clock = new FakeClock();
    let refreshes = 0;
    const live = new LiveSocket({
        url: (token) => liveUrl("https://api.example.test", "pod-1", token),
        auth: {
            token: async () => options.token ?? "tok",
            refresh: async () => { refreshes += 1; await (options.refresh ?? (async () => {}))(); },
        },
        open: (url) => { const socket = new FakeSocket(url); sockets.push(socket); return socket; },
        clock,
        random: () => 1,
        idleCloseMs: 1_000,
    });
    return { live, sockets, clock, refreshes: () => refreshes, last: () => sockets.at(-1)! };
}

function recorder() {
    const heard: { kind: string; value: unknown }[] = [];
    const handlers: LiveHandlers = {
        onSubscribed: (cursor) => heard.push({ kind: "subscribed", value: cursor }),
        onEvents: (events, cursor) => heard.push({ kind: "events", value: { events, cursor } }),
        onResync: (reason, cursor) => heard.push({ kind: "resync", value: { reason, cursor } }),
        onEnded: (reason) => heard.push({ kind: "ended", value: reason }),
    };
    return { heard, handlers };
}

test("the URL is the pod's live socket, with the token where a browser cannot set a header", () => {
    assert.equal(liveUrl("https://api.example.test/", "pod 1", "a.b"), "wss://api.example.test/pods/pod%201/live?access_token=a.b");
    assert.equal(liveUrl("http://localhost:8000", "p", null), "ws://localhost:8000/pods/p/live");
});

test("backoff is full jitter under a doubling ceiling that stops at thirty seconds", () => {
    assert.equal(reconnectDelay(0, () => 1), 500);
    assert.equal(reconnectDelay(3, () => 1), 4_000);
    assert.equal(reconnectDelay(20, () => 1), 30_000);
    assert.equal(reconnectDelay(5, () => 0), 0);
});

test("subscriptions go out only after ready, never on the socket merely opening", async () => {
    const { live, last } = harness();
    const channel = recorder();
    live.subscribe("channel", { channel_id: "c1" }, channel.handlers);
    await settle();
    assert.equal(live.status, "connecting");
    assert.equal(last().url, "wss://api.example.test/pods/pod-1/live?access_token=tok");
    assert.deepEqual(last().sent, [], "nothing is sent before the server says ready");
    last().hear({ type: "ready", connection: "x", topics: ["channel"], calls: [] });
    assert.equal(live.status, "open");
    assert.deepEqual(last().subscribes(), [{ op: "subscribe", id: "c1", topic: "channel", channel_id: "c1" }]);
});

test("each subscription keeps its own cursor, and a reconnect resumes every one from it", async () => {
    const { live, sockets, clock, last } = harness();
    const channel = recorder();
    const inbox = recorder();
    live.subscribe("channel", { channel_id: "c1" }, channel.handlers);
    live.subscribe("inbox", {}, inbox.handlers);
    await settle();
    last().hear({ type: "ready" });
    const [first, second] = last().subscribes();
    last().hear({ type: "subscribed", id: first.id, cursor: "4:10" });
    last().hear({ type: "subscribed", id: second.id, cursor: null });
    last().hear({ type: "events", id: first.id, cursor: "5:11", events: [{ type: "line.created", message: { id: "l11" } }] });
    assert.deepEqual(channel.heard.map((one) => one.kind), ["subscribed", "events"]);

    last().drop(1006);
    assert.equal(live.status, "reconnecting");
    clock.advance(500);
    await settle();
    assert.equal(sockets.length, 2, "one new socket, after the backoff");
    last().hear({ type: "ready" });
    const again = last().subscribes();
    assert.deepEqual(again.map((frame) => [frame.topic, frame.since]), [["channel", "5:11"], ["inbox", undefined]]);
});

test("a split batch moves the cursor only on its last frame, so a drop mid-batch replays all of it", async () => {
    const { live, last, clock } = harness();
    const channel = recorder();
    live.subscribe("channel", { channel_id: "c1" }, channel.handlers);
    await settle();
    last().hear({ type: "ready" });
    const id = last().subscribes()[0].id;
    last().hear({ type: "subscribed", id, cursor: "1:1" });
    /* The first part of a batch carries the cursor from before the batch. */
    last().hear({ type: "events", id, cursor: "1:1", events: [{ type: "line.created" }] });
    last().drop(1006);
    clock.advance(500);
    await settle();
    last().hear({ type: "ready" });
    assert.equal(last().subscribes()[0].since, "1:1");
});

test("a resync is handed to its subscription to refetch, and the subscription carries on", async () => {
    const { live, last } = harness();
    const channel = recorder();
    live.subscribe("channel", { channel_id: "c1" }, channel.handlers, "9:9");
    await settle();
    last().hear({ type: "ready" });
    const frame = last().subscribes()[0];
    assert.equal(frame.since, "9:9", "a subscription made with a cursor resumes from it");
    last().hear({ type: "resync", id: frame.id, cursor: "12:30", reason: "too_many" });
    last().hear({ type: "events", id: frame.id, cursor: "13:31", events: [{ type: "line.created" }] });
    assert.deepEqual(channel.heard.map((one) => one.kind), ["resync", "events"]);
    assert.deepEqual(channel.heard[0].value, { reason: "too_many", cursor: "12:30" });
});

test("a channel deleted or no longer the reader's ends its subscription; the socket stays", async () => {
    const { live, last } = harness();
    const gone = recorder();
    const other = recorder();
    live.subscribe("channel", { channel_id: "c1" }, gone.handlers);
    live.subscribe("channel", { channel_id: "c2" }, other.handlers);
    await settle();
    last().hear({ type: "ready" });
    const [first, second] = last().subscribes();
    last().hear({ type: "unsubscribed", id: first.id, reason: "access_revoked" });
    last().hear({ type: "error", id: second.id, code: "not_found", message: "Channel not found" });
    assert.deepEqual(gone.heard, [{ kind: "ended", value: "access_revoked" }]);
    assert.deepEqual(other.heard, [{ kind: "ended", value: "not_found" }]);
    assert.equal(last().closed, null);
});

test("4401 refreshes the session once and reconnects; a second 4401 before ready gives up", async () => {
    const { live, sockets, clock, last, refreshes } = harness();
    const channel = recorder();
    live.subscribe("channel", { channel_id: "c1" }, channel.handlers);
    await settle();
    last().drop(4401);
    await settle();
    assert.equal(refreshes(), 1);
    clock.advance(500);
    await settle();
    assert.equal(sockets.length, 2);
    last().drop(4401);
    await settle();
    assert.equal(refreshes(), 1, "not refreshed twice in a row");
    assert.equal(live.status, "closed");
    assert.deepEqual(channel.heard, [{ kind: "ended", value: "unauthenticated" }]);
});

test("4403 and 4404 are final; 4429 waits out other tabs rather than retrying at once", async () => {
    const forbidden = harness();
    const seen = recorder();
    forbidden.live.subscribe("inbox", {}, seen.handlers);
    await settle();
    forbidden.last().drop(4403);
    assert.equal(forbidden.live.status, "closed");
    assert.deepEqual(seen.heard, [{ kind: "ended", value: "forbidden" }]);
    forbidden.clock.advance(60_000);
    assert.equal(forbidden.sockets.length, 1, "never reconnects");

    const crowded = harness();
    crowded.live.subscribe("inbox", {}, recorder().handlers);
    await settle();
    crowded.last().drop(4429);
    crowded.clock.advance(14_000);
    await settle();
    assert.equal(crowded.sockets.length, 1);
    crowded.clock.advance(1_000);
    await settle();
    assert.equal(crowded.sockets.length, 2);
});

test("calls are answered on their own id; with no open socket they are refused at once", async () => {
    const { live, last } = harness();
    await assert.rejects(live.call("read", { channel_id: "c1", seq: 4 }), (error: unknown) => error instanceof LiveCallError && error.code === "unavailable");
    live.subscribe("channel", { channel_id: "c1" }, recorder().handlers);
    await settle();
    last().hear({ type: "ready" });
    const read = live.call("read", { channel_id: "c1", seq: 4 });
    const typing = live.call("typing", { channel_id: "c1" });
    const [readFrame, typingFrame] = last().sent.filter((frame) => frame.op === "call");
    assert.deepEqual({ method: readFrame.method, params: readFrame.params }, { method: "read", params: { channel_id: "c1", seq: 4 } });
    last().hear({ type: "error", id: typingFrame.id, code: "limit", message: "Too many" });
    last().hear({ type: "result", id: readFrame.id, result: { channel_id: "c1", read_seq: 4 } });
    assert.deepEqual(await read, { channel_id: "c1", read_seq: 4 });
    await assert.rejects(typing, (error: unknown) => error instanceof LiveCallError && error.code === "limit");
});

test("a call in flight when the socket drops is refused, not left hanging", async () => {
    const { live, last } = harness();
    live.subscribe("channel", { channel_id: "c1" }, recorder().handlers);
    await settle();
    last().hear({ type: "ready" });
    const presence = live.call("presence", {});
    last().drop(1006);
    await assert.rejects(presence, (error: unknown) => error instanceof LiveCallError && error.code === "unavailable");
});

test("closing the last subscription unsubscribes it and lets the socket go after a short idle", async () => {
    const { live, last, clock } = harness();
    const subscription = live.subscribe("channel", { channel_id: "c1" }, recorder().handlers);
    await settle();
    last().hear({ type: "ready" });
    subscription.close();
    assert.deepEqual(last().sent.at(-1), { op: "unsubscribe", id: subscription.id });
    clock.advance(999);
    assert.equal(last().closed, null, "a remount within the idle reuses it");
    clock.advance(1);
    assert.equal(last().closed, 1000);
    assert.equal(live.status, "idle");
});

test("pings keep the socket from idling out behind a proxy", async () => {
    const { live, last, clock } = harness();
    live.subscribe("inbox", {}, recorder().handlers);
    await settle();
    last().hear({ type: "ready" });
    clock.advance(25_000);
    assert.deepEqual(last().sent.at(-1), { op: "ping" });
});

test("the status is told to whoever listens: polling falls back only while it is not open", async () => {
    const { live, last } = harness();
    const said: string[] = [];
    live.onStatus((status) => said.push(status));
    live.subscribe("inbox", {}, recorder().handlers);
    await settle();
    last().hear({ type: "ready" });
    last().drop(1011);
    assert.deepEqual(said, ["idle", "connecting", "open", "reconnecting"]);
});

test("a socket that goes silent is dropped and dialled again: one ping unanswered is enough", async () => {
    const { live, sockets, last, clock } = harness();
    const { heard, handlers } = recorder();
    live.subscribe("inbox", {}, handlers);
    await settle();
    last().hear({ type: "ready" });
    clock.advance(25_000);
    assert.deepEqual(last().sent.at(-1), { op: "ping" });
    last().hear({ type: "pong" });
    clock.advance(25_000);
    assert.equal(live.status, "open", "an answered ping keeps it");
    /* Half-open: the ping goes nowhere and no close ever comes. */
    clock.advance(25_000);
    assert.equal(sockets[0].closed, 4000, "the silent socket is closed here");
    assert.equal(live.status, "reconnecting");
    clock.advance(1_000);
    await settle();
    assert.equal(sockets.length, 2, "and dialled again");
    sockets[1].hear({ type: "ready" });
    assert.equal(sockets[1].subscribes().length, 1, "with its subscription sent again");
    assert.ok(heard.every((one) => one.kind !== "ended"));
});

test("back online or back to the tab: a socket waiting to reconnect dials now, an open one is asked to answer", async () => {
    let wake: (() => void) | null = null;
    const sockets: FakeSocket[] = [];
    const clock = new FakeClock();
    const live = new LiveSocket({
        url: (token) => liveUrl("https://api.example.test", "pod-1", token),
        auth: { token: async () => "tok", refresh: async () => {} },
        open: (url) => { const socket = new FakeSocket(url); sockets.push(socket); return socket; },
        clock,
        random: () => 1,
        wakeups: (given) => { wake = given; return () => { wake = null; }; },
    });
    live.subscribe("inbox", {}, recorder().handlers);
    await settle();
    sockets[0].hear({ type: "ready" });
    wake!();
    assert.deepEqual(sockets[0].sent.at(-1), { op: "ping" }, "an open socket is pinged at once");
    clock.advance(25_000);
    assert.equal(sockets[0].closed, 4000, "and dropped when it does not answer");
    assert.equal(sockets.length, 1, "still waiting out its backoff");
    wake!();
    await settle();
    assert.equal(sockets.length, 2, "coming back dials now, not after the backoff");
    live.close();
    assert.equal(wake, null, "a closed socket stops listening");
});
