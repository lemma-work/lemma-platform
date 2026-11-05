import test from "node:test";
import assert from "node:assert/strict";
import { SocketUnavailable, socketCarries, socketRunStream, sseFrame, type LiveTopicSource, type RunStreamDeps } from "../src/live/conversation-stream.ts";
import type { LiveHandlers, LiveStatus } from "../src/live/socket.ts";

/** The conversation pane's run, read off the live socket as the SSE bytes
 *  the SDK's session already reads: the same frames, the same ending. */

function fakeLive(options: { refuse?: string; silent?: boolean } = {}) {
    let handlers: LiveHandlers | null = null;
    let params: Record<string, unknown> | null = null;
    let closed = false;
    const listeners = new Set<(status: LiveStatus) => void>();
    const live: LiveTopicSource = {
        status: "open",
        subscribe(topic, given, heard) {
            assert.equal(topic, "conversation");
            params = given;
            handlers = heard;
            queueMicrotask(() => {
                if (options.refuse) heard.onEnded?.(options.refuse);
                else if (!options.silent) heard.onSubscribed?.(null);
            });
            return { id: "v1", cursor: null, close: () => { closed = true; } };
        },
        onStatus(listener) { listeners.add(listener); listener("open"); return () => listeners.delete(listener); },
    };
    return {
        live,
        params: () => params,
        closed: () => closed,
        events: (...events: Record<string, unknown>[]) => handlers?.onEvents?.(events, null),
        resync: () => handlers?.onResync?.("overflow", null),
        status: (status: LiveStatus) => { for (const listener of listeners) listener(status); },
    };
}

async function readAll(stream: ReadableStream<Uint8Array>): Promise<Record<string, unknown>[]> {
    const text = await new Response(stream).text();
    return text.split("\n\n").filter(Boolean).map((frame) => JSON.parse(frame.replace(/^data: /, "")));
}

function deps(live: LiveTopicSource, overrides: Partial<RunStreamDeps> = {}): RunStreamDeps {
    return {
        live,
        status: async () => "RUNNING",
        append: async () => ({ agent_run_id: "run-1", started_new_run: true, deduplicated: false }),
        subscribeMs: 200,
        ...overrides,
    };
}

test("a frame is written the way the SSE endpoint writes it", () => {
    assert.equal(sseFrame({ type: "token", data: "Hi", kind: "text", agent_run_id: "r" }), 'data: {"type":"token","data":"Hi","kind":"text"}\n\n');
    assert.equal(sseFrame({ type: "status", data: { status: "RUNNING" }, agent_run_id: "r" }), 'data: {"type":"status","data":{"status":"RUNNING"},"agent_run_id":"r"}\n\n');
    assert.equal(sseFrame({ type: "title", data: "Plans" }), 'data: {"type":"title","data":"Plans","agent_run_id":null}\n\n');
});

test("a send is subscribed first, sent second, and says first whether it started a run", async () => {
    const fake = fakeLive();
    const order: string[] = [];
    const stream = await socketRunStream(deps(fake.live, {
        append: async (id, payload) => { order.push("append " + id + " " + JSON.stringify(payload)); return { agent_run_id: "run-1", started_new_run: false, deduplicated: false }; },
    }), { conversationId: "conv-1", send: { content: "hello" } });
    assert.deepEqual(fake.params(), { conversation_id: "conv-1" });
    assert.deepEqual(order, ['append conv-1 {"content":"hello"}']);
    /* Which run the socket watched before the send: none. A send is not
       ended by it. */
    fake.events({ type: "run", agent_run_id: null, status: null });
    fake.events(
        { type: "token", data: "Hel", kind: "text", agent_run_id: "run-1" },
        { type: "token", data: "nope", kind: "text", agent_run_id: "run-other" },
        { type: "title", data: "Greetings" },
        { type: "message", data: { id: "m1", role: "assistant" }, agent_run_id: "run-1" },
        { type: "completed", data: { status: "COMPLETED" }, agent_run_id: "run-1" },
        { type: "token", data: "after the end", kind: "text", agent_run_id: "run-1" },
    );
    const frames = await readAll(stream);
    assert.deepEqual(frames.map((frame) => frame.type), ["status", "token", "title", "message", "completed"]);
    assert.deepEqual(frames[0], { type: "status", data: { started_new_run: false, deduplicated: false }, agent_run_id: "run-1" });
    assert.equal(fake.closed(), true, "the subscription goes with the run");
});

test("attaching when no run is going ends at once, as the SSE stream closes", async () => {
    const fake = fakeLive();
    const stream = await socketRunStream(deps(fake.live), { conversationId: "conv-1" });
    fake.events({ type: "run", agent_run_id: null, status: null });
    assert.deepEqual(await readAll(stream), []);
    assert.equal(fake.closed(), true);
});

test("attaching to a named run asks the socket for it, and its replayed ending ends the stream", async () => {
    const fake = fakeLive();
    const stream = await socketRunStream(deps(fake.live), { conversationId: "conv-1", agentRunId: "run-3" });
    assert.deepEqual(fake.params(), { conversation_id: "conv-1", agent_run_id: "run-3" });
    fake.events({ type: "run", agent_run_id: "run-3", status: "COMPLETED" }, { type: "completed", data: { status: "COMPLETED" }, agent_run_id: "run-3" });
    assert.deepEqual((await readAll(stream)).map((frame) => frame.type), ["completed"], "the socket's own run frame is not an SSE frame");
});

test("a send's own events that arrive before it is answered wait behind its status frame", async () => {
    const fake = fakeLive();
    let answer!: (start: { agent_run_id: string }) => void;
    const pending = socketRunStream(deps(fake.live, { append: () => new Promise((resolve) => { answer = resolve; }) }), { conversationId: "conv-1", send: { content: "x" } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    fake.events({ type: "token", data: "early", kind: "text", agent_run_id: "run-9" }, { type: "token", data: "someone else", kind: "text", agent_run_id: "run-0" });
    answer({ agent_run_id: "run-9" });
    const stream = await pending;
    fake.events({ type: "completed", data: {}, agent_run_id: "run-9" });
    assert.deepEqual((await readAll(stream)).map((frame) => frame.type + ":" + JSON.stringify(frame.data)), ['status:{"started_new_run":true,"deduplicated":false}', 'token:"early"', "completed:{}"]);
});

test("a resumed run is followed by the first run it hears, and another run's events are left out", async () => {
    const fake = fakeLive();
    const stream = await socketRunStream(deps(fake.live), { conversationId: "conv-1" });
    fake.events(
        { type: "run", agent_run_id: "run-7", status: "RUNNING" },
        { type: "token", data: "a", kind: "text", agent_run_id: "run-7" },
        { type: "token", data: "b", kind: "text", agent_run_id: "run-8" },
        { type: "stopped", data: {}, agent_run_id: "run-7" },
    );
    const frames = await readAll(stream);
    assert.deepEqual(frames.map((frame) => frame.data), ["a", {}]);
});

test("a socket that drops or falls behind ends the stream with no ending, so the session catches up over REST", async () => {
    const dropped = fakeLive();
    const first = await socketRunStream(deps(dropped.live), { conversationId: "conv-1", agentRunId: "run-1" });
    dropped.events({ type: "token", data: "x", kind: "text", agent_run_id: "run-1" });
    dropped.status("reconnecting");
    assert.deepEqual((await readAll(first)).map((frame) => frame.type), ["token"]);

    const behind = fakeLive();
    const second = await socketRunStream(deps(behind.live), { conversationId: "conv-1", agentRunId: "run-1" });
    behind.resync();
    assert.deepEqual(await readAll(second), []);
});

test("a socket that refuses, or does not answer in time, sends the caller back to SSE", async () => {
    await assert.rejects(socketRunStream(deps(fakeLive({ refuse: "not_found" }).live), { conversationId: "conv-1" }), SocketUnavailable);
    let sent = false;
    await assert.rejects(
        socketRunStream(deps(fakeLive({ silent: true }).live, { append: async () => { sent = true; return { agent_run_id: "r" }; } }), { conversationId: "conv-1", send: { content: "x" } }),
        SocketUnavailable,
    );
    assert.equal(sent, false, "nothing is sent before the socket listens, so SSE can send it instead");
});

test("a send the server refuses is the caller's error, as the SSE route's would be", async () => {
    const fake = fakeLive();
    const refused = Object.assign(new Error("Usage limit"), { statusCode: 429 });
    await assert.rejects(socketRunStream(deps(fake.live, { append: async () => { throw refused; } }), { conversationId: "conv-1", send: { content: "x" } }), refused);
    assert.equal(fake.closed(), true);
});

test("a socket that is down sends the caller to SSE at once, without waiting out the subscribe timeout", async () => {
    for (const status of ["reconnecting", "closed"] as const) {
        const fake = fakeLive({ silent: true });
        let subscribed = false;
        const live: LiveTopicSource = { ...fake.live, status, subscribe: (...args) => { subscribed = true; return fake.live.subscribe(...args); } };
        let timers = 0;
        await assert.rejects(
            socketRunStream(deps(live, { setTimeout: () => { timers += 1; return 0; }, clearTimeout: () => {} }), { conversationId: "conv-1", send: { content: "x" } }),
            SocketUnavailable,
        );
        assert.equal(subscribed, false, status + ": nothing subscribed");
        assert.equal(timers, 0, status + ": no wait armed");
    }
    assert.equal(socketCarries("idle"), true, "a socket not yet opened is opened by the subscription");
    assert.equal(socketCarries("connecting"), true);
    assert.equal(socketCarries("open"), true);
});
