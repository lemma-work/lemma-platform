import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
    watchDatastoreChanges,
    type ChangeStreamTokenProvider,
} from "../datastore-changes.js";

// jsdom has no WebSocket, so stub a controllable fake that records instances,
// what was sent on each, and lets the test drive open/message/close.
class FakeWebSocket {
    static instances: FakeWebSocket[] = [];
    url: string;
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    onerror: (() => void) | null = null;
    closed = false;
    closeCode?: number;
    sent: Array<Record<string, unknown>> = [];

    constructor(url: string) {
        this.url = url;
        FakeWebSocket.instances.push(this);
    }

    send(text: string): void {
        this.sent.push(JSON.parse(text) as Record<string, unknown>);
    }

    close(code?: number): void {
        this.closed = true;
        this.closeCode = code;
    }

    receive(frame: Record<string, unknown>): void {
        this.onmessage?.({ data: JSON.stringify(frame) });
    }

    subscribes(): Array<Record<string, unknown>> {
        return this.sent.filter((frame) => frame.op === "subscribe");
    }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
// refreshAccessToken (microtask) -> scheduleReconnect -> setTimeout(0) -> connect
const settle = async () => {
    await flush();
    await flush();
    await flush();
};
const last = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

let originalWebSocket: unknown;

function makeAuth(): ChangeStreamTokenProvider & {
    getAccessToken: ReturnType<typeof vi.fn>;
    refreshAccessToken: ReturnType<typeof vi.fn>;
} {
    return {
        getAccessToken: vi.fn(async () => "TOKEN"),
        refreshAccessToken: vi.fn(async () => "TOKEN2"),
    };
}

beforeEach(() => {
    FakeWebSocket.instances = [];
    originalWebSocket = (globalThis as { WebSocket?: unknown }).WebSocket;
    (globalThis as { WebSocket?: unknown }).WebSocket =
        FakeWebSocket as unknown as typeof WebSocket;
    vi.spyOn(Math, "random").mockReturnValue(0); // make reconnect delay 0
});

afterEach(() => {
    (globalThis as { WebSocket?: unknown }).WebSocket = originalWebSocket;
    vi.restoreAllMocks();
    vi.useRealTimers();
});

describe("watchDatastoreChanges", () => {
    it("sends a `when` filter with its subscription", async () => {
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            {
                table: "notes",
                when: { status: { in: ["open"] } },
                onChange: vi.fn(),
            },
        );
        await flush();
        const ws = FakeWebSocket.instances[0];
        ws.onopen?.();
        ws.receive({ type: "ready", connection: "conn" });
        expect(ws.subscribes()).toEqual([
            {
                op: "subscribe",
                id: "s1",
                topic: "records",
                table: "notes",
                when: { status: { in: ["open"] } },
            },
        ]);
        handle.close();
    });

    it("subscribes on the pod's live socket and delivers changes in the old frame shape", async () => {
        const auth = makeAuth();
        const onChange = vi.fn();
        const onReady = vi.fn();
        const onStatus = vi.fn();

        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                table: "notes",
                since: "c1.old",
                onChange,
                onReady,
                onStatus,
            },
        );
        await flush();

        const ws = FakeWebSocket.instances[0];
        expect(ws.url).toBe(
            "wss://api.x.test/pods/POD/live?access_token=TOKEN",
        );
        ws.onopen?.();
        expect(ws.sent).toEqual([]); // nothing until the server says the session is taken
        ws.receive({ type: "ready", connection: "conn" });
        expect(ws.subscribes()).toEqual([
            {
                op: "subscribe",
                id: "s1",
                topic: "records",
                table: "notes",
                since: "c1.old",
            },
        ]);
        expect(onStatus).not.toHaveBeenCalledWith("open");

        ws.receive({ type: "subscribed", id: "s1", cursor: "c1.a" });
        expect(onReady).toHaveBeenCalledWith({ since: "c1.a" });
        expect(onStatus).toHaveBeenLastCalledWith("open");

        ws.receive({
            type: "changes",
            id: "s1",
            cursor: "c1.b",
            changes: [
                {
                    op: "insert",
                    table: "notes",
                    record_id: "a",
                    row: { body: "hi" },
                },
            ],
        });
        expect(onChange).toHaveBeenCalledWith({
            type: "datastore.record.insert",
            pod_id: "POD",
            table_name: "notes",
            record_id: "a",
            operation: "insert",
            payload: { body: "hi" },
            cursor: "c1.b",
            stream_id: "c1.b",
        });

        handle.close();
    });

    it("converts http to ws (insecure) for local servers", async () => {
        const handle = watchDatastoreChanges(
            "http://localhost:8711",
            makeAuth(),
            "POD",
            {
                onChange: vi.fn(),
            },
        );
        await flush();
        expect(FakeWebSocket.instances[0].url).toBe(
            "ws://localhost:8711/pods/POD/live?access_token=TOKEN",
        );
        handle.close();
    });

    it("shares one socket per pod and ref-counts it", async () => {
        vi.useFakeTimers();
        const auth = makeAuth();
        const tickets = vi.fn();
        const comments = vi.fn();
        const a = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            table: "tickets",
            onChange: tickets,
        });
        const b = watchDatastoreChanges("https://api.x.test/", auth, "POD", {
            table: "comments",
            onChange: comments,
        });
        const other = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "OTHER",
            {
                onChange: vi.fn(),
            },
        );
        await vi.advanceTimersByTimeAsync(0);
        expect(FakeWebSocket.instances.map((ws) => ws.url)).toEqual([
            "wss://api.x.test/pods/POD/live?access_token=TOKEN",
            "wss://api.x.test/pods/OTHER/live?access_token=TOKEN",
        ]);

        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        expect(ws.subscribes().map((f) => [f.id, f.table])).toEqual([
            ["s1", "tickets"],
            ["s2", "comments"],
        ]);
        ws.receive({
            type: "changes",
            id: "s2",
            cursor: "c",
            changes: [
                { op: "update", table: "comments", record_id: "x", row: {} },
            ],
        });
        expect(comments).toHaveBeenCalledTimes(1);
        expect(tickets).not.toHaveBeenCalled();

        // A third watch on the open socket subscribes immediately.
        const c = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            table: "orders",
            onChange: vi.fn(),
        });
        expect(ws.subscribes().at(-1)).toMatchObject({
            id: "s3",
            table: "orders",
        });

        a.close();
        expect(ws.sent.at(-1)).toEqual({ op: "unsubscribe", id: "s1" });
        b.close();
        c.close();
        expect(ws.closed).toBe(false);
        // A remount inside the idle window reuses the socket.
        const d = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            onChange: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(2_000);
        expect(ws.closed).toBe(false);
        expect(FakeWebSocket.instances.length).toBe(2);
        d.close();
        await vi.advanceTimersByTimeAsync(1_000);
        expect(ws.closed).toBe(true);

        // The next watch opens a fresh socket.
        const e = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            onChange: vi.fn(),
        });
        await vi.advanceTimersByTimeAsync(0);
        expect(FakeWebSocket.instances.length).toBe(3);
        e.close();
        other.close();
    });

    it("resubscribes every subscription from its own cursor after a drop", async () => {
        const auth = makeAuth();
        const onStatus = vi.fn();
        const a = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            table: "tickets",
            onChange: vi.fn(),
            onStatus,
        });
        const b = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            table: "comments",
            onChange: vi.fn(),
        });
        await flush();
        const first = FakeWebSocket.instances[0];
        first.receive({ type: "ready" });
        first.receive({ type: "subscribed", id: "s1", cursor: "c.1" });
        first.receive({ type: "subscribed", id: "s2", cursor: "c.1" });
        first.receive({
            type: "changes",
            id: "s1",
            cursor: "c.5",
            changes: [
                { op: "insert", table: "tickets", record_id: "r", row: {} },
            ],
        });
        first.onclose?.({ code: 1006 });
        expect(onStatus).toHaveBeenLastCalledWith("reconnecting");
        await settle();

        const second = last();
        expect(second).not.toBe(first);
        second.receive({ type: "ready" });
        expect(second.subscribes().map((f) => [f.id, f.since])).toEqual([
            ["s1", "c.5"],
            ["s2", "c.1"],
        ]);
        a.close();
        b.close();
    });

    it("hands a resync to the subscription and resumes from its cursor", async () => {
        const auth = makeAuth();
        const onResync = vi.fn();
        const onChange = vi.fn();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                table: "tickets",
                onChange,
                onResync,
            },
        );
        await flush();
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        ws.receive({
            type: "resync",
            id: "s1",
            cursor: "c.9",
            reason: "too_many",
        });
        expect(onResync).toHaveBeenCalledWith({
            cursor: "c.9",
            reason: "too_many",
        });
        expect(onChange).not.toHaveBeenCalled();

        ws.onclose?.({ code: 1006 });
        await settle();
        last().receive({ type: "ready" });
        expect(last().subscribes()[0].since).toBe("c.9");
        handle.close();
    });

    it("accepts the legacy stream_id as the cursor", async () => {
        const auth = makeAuth();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                onChange: vi.fn(),
            },
        );
        await flush();
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        ws.receive({ type: "subscribed", id: "s1", stream_id: "c.legacy" });
        ws.onclose?.({ code: 1006 });
        await settle();
        last().receive({ type: "ready" });
        expect(last().subscribes()[0].since).toBe("c.legacy");
        handle.close();
    });

    it("ends only the subscription the server refuses or ends", async () => {
        const auth = makeAuth();
        const refusedError = vi.fn();
        const revokedError = vi.fn();
        const fine = vi.fn();
        const refused = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                table: "nope",
                onChange: vi.fn(),
                onError: refusedError,
            },
        );
        const revoked = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                table: "secret",
                onChange: vi.fn(),
                onError: revokedError,
            },
        );
        const kept = watchDatastoreChanges("https://api.x.test", auth, "POD", {
            table: "tickets",
            onChange: fine,
        });
        await flush();
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        ws.receive({
            type: "error",
            id: "s1",
            code: "not_found",
            message: "no table",
        });
        ws.receive({ type: "subscribed", id: "s2", cursor: "c" });
        ws.receive({
            type: "unsubscribed",
            id: "s2",
            reason: "access_revoked",
        });

        expect(refused.closed).toBe(true);
        expect(refusedError.mock.calls[0][0].message).toMatch(/not_found/);
        expect(revoked.closed).toBe(true);
        expect(revokedError.mock.calls[0][0].message).toMatch(/access_revoked/);
        expect(kept.closed).toBe(false);
        expect(ws.closed).toBe(false);
        ws.receive({
            type: "changes",
            id: "s3",
            cursor: "c",
            changes: [
                { op: "delete", table: "tickets", record_id: "r", row: null },
            ],
        });
        expect(fine).toHaveBeenCalledWith(
            expect.objectContaining({ operation: "delete", payload: {} }),
        );
        kept.close();
    });

    it("retries an unavailable subscription on the same socket with backoff", async () => {
        vi.useFakeTimers();
        vi.spyOn(Math, "random").mockReturnValue(1); // delay = the full ceiling
        const auth = makeAuth();
        const onChange = vi.fn();
        const onError = vi.fn();
        const onStatus = vi.fn();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            { table: "tickets", since: "c.0", onChange, onError, onStatus },
        );
        const neighbour = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            { table: "comments", onChange: vi.fn() },
        );
        await vi.advanceTimersByTimeAsync(0);
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        ws.receive({ type: "subscribed", id: "s2", cursor: "c.1" });
        const tickets = () => ws.subscribes().filter((f) => f.id === "s1");
        expect(tickets()).toHaveLength(1);

        ws.receive({ type: "error", id: "s1", code: "unavailable" });
        expect(onStatus).toHaveBeenLastCalledWith("reconnecting");
        await vi.advanceTimersByTimeAsync(499);
        expect(tickets()).toHaveLength(1);
        await vi.advanceTimersByTimeAsync(1);
        expect(tickets()).toHaveLength(2);
        expect(tickets()[1]).toMatchObject({ table: "tickets", since: "c.0" });

        /* A second refusal doubles the wait. */
        ws.receive({ type: "error", id: "s1", code: "unavailable" });
        await vi.advanceTimersByTimeAsync(999);
        expect(tickets()).toHaveLength(2);
        await vi.advanceTimersByTimeAsync(1);
        expect(tickets()).toHaveLength(3);

        ws.receive({ type: "subscribed", id: "s1", cursor: "c.2" });
        expect(onStatus).toHaveBeenLastCalledWith("open");
        ws.receive({
            type: "changes",
            id: "s1",
            cursor: "c.3",
            changes: [
                { op: "insert", table: "tickets", record_id: "r", row: {} },
            ],
        });
        expect(onChange).toHaveBeenCalledWith(
            expect.objectContaining({ record_id: "r", cursor: "c.3" }),
        );

        /* `subscribed` reset the backoff, and the retry resumes from the cursor. */
        ws.receive({ type: "error", id: "s1", code: "unavailable" });
        await vi.advanceTimersByTimeAsync(500);
        expect(tickets()).toHaveLength(4);
        expect(tickets()[3].since).toBe("c.3");

        expect(onError).not.toHaveBeenCalled();
        expect(handle.closed).toBe(false);
        expect(neighbour.closed).toBe(false);
        expect(ws.closed).toBe(false);
        expect(FakeWebSocket.instances.length).toBe(1);
        handle.close();
        neighbour.close();
    });

    it("keeps every other subscription error terminal", async () => {
        vi.useFakeTimers();
        const onError = vi.fn();
        const onStatus = vi.fn();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            { table: "secret", onChange: vi.fn(), onError, onStatus },
        );
        await vi.advanceTimersByTimeAsync(0);
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        ws.receive({ type: "error", id: "s1", code: "forbidden" });

        expect(handle.closed).toBe(true);
        expect(onStatus).toHaveBeenLastCalledWith("closed");
        expect(onError.mock.calls[0][0].message).toMatch(/forbidden/);
        await vi.advanceTimersByTimeAsync(60_000);
        expect(ws.subscribes()).toHaveLength(1);
    });

    it("gives up an unavailable subscription after its maxRetries", async () => {
        vi.useFakeTimers();
        const onError = vi.fn();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            { onChange: vi.fn(), onError, maxRetries: 1 },
        );
        await vi.advanceTimersByTimeAsync(0);
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        ws.receive({ type: "error", id: "s1", code: "unavailable" });
        await vi.advanceTimersByTimeAsync(0);
        expect(ws.subscribes()).toHaveLength(2);
        ws.receive({ type: "error", id: "s1", code: "unavailable" });
        expect(handle.closed).toBe(true);
        expect(onError.mock.calls[0][0].message).toMatch(/max retry attempts/);
    });

    it("cancels a pending unavailable retry on unsubscribe", async () => {
        vi.useFakeTimers();
        const auth = makeAuth();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            { table: "tickets", onChange: vi.fn() },
        );
        const neighbour = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            { table: "comments", onChange: vi.fn() },
        );
        await vi.advanceTimersByTimeAsync(0);
        const ws = FakeWebSocket.instances[0];
        ws.receive({ type: "ready" });
        vi.spyOn(Math, "random").mockReturnValue(1);
        ws.receive({ type: "error", id: "s1", code: "unavailable" });
        handle.close();
        expect(ws.sent.at(-1)).toEqual({ op: "unsubscribe", id: "s1" });

        await vi.advanceTimersByTimeAsync(60_000);
        expect(ws.subscribes().filter((f) => f.id === "s1")).toHaveLength(1);
        neighbour.close();
    });

    it("drops a pending unavailable retry when the socket reconnects", async () => {
        vi.useFakeTimers();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            { table: "tickets", onChange: vi.fn() },
        );
        await vi.advanceTimersByTimeAsync(0);
        const first = FakeWebSocket.instances[0];
        first.receive({ type: "ready" });
        vi.spyOn(Math, "random").mockReturnValue(1);
        first.receive({ type: "error", id: "s1", code: "unavailable" });
        vi.spyOn(Math, "random").mockReturnValue(0);
        first.onclose?.({ code: 1006 });
        await vi.advanceTimersByTimeAsync(0);
        const second = last();
        expect(second).not.toBe(first);
        second.receive({ type: "ready" });
        expect(second.subscribes()).toHaveLength(1);

        await vi.advanceTimersByTimeAsync(60_000);
        expect(second.subscribes()).toHaveLength(1);
        expect(first.subscribes()).toHaveLength(1);
        handle.close();
    });

    it("refreshes the token once after a 4401 and fails every watch on the second", async () => {
        const auth = makeAuth();
        const errors = [vi.fn(), vi.fn()];
        const handles = errors.map((onError) =>
            watchDatastoreChanges("https://api.x.test", auth, "POD", {
                onChange: vi.fn(),
                onError,
            }),
        );
        await flush();

        FakeWebSocket.instances[0].onopen?.();
        FakeWebSocket.instances[0].onclose?.({ code: 4401 });
        await settle();
        expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1);
        expect(FakeWebSocket.instances.length).toBe(2);

        FakeWebSocket.instances[1].onopen?.();
        FakeWebSocket.instances[1].onclose?.({ code: 4401 });
        await settle();

        expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1);
        expect(FakeWebSocket.instances.length).toBe(2);
        for (const [index, onError] of errors.entries()) {
            expect(handles[index].closed).toBe(true);
            expect(onError.mock.calls[0][0].message).toMatch(
                /session rejected after refresh/,
            );
        }
    });

    it("allows another refresh once the socket has been ready again", async () => {
        const auth = makeAuth();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                onChange: vi.fn(),
            },
        );
        await flush();

        FakeWebSocket.instances[0].onclose?.({ code: 4401 });
        await settle();
        FakeWebSocket.instances[1].receive({ type: "ready" });
        FakeWebSocket.instances[1].onclose?.({ code: 4401 });
        await settle();

        expect(auth.refreshAccessToken).toHaveBeenCalledTimes(2);
        expect(FakeWebSocket.instances.length).toBe(3);
        expect(handle.closed).toBe(false);
        handle.close();
    });

    it("stops when the refresh itself fails", async () => {
        const auth = makeAuth();
        auth.refreshAccessToken.mockRejectedValue(
            new Error("Session refresh failed"),
        );
        const onError = vi.fn();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                onChange: vi.fn(),
                onError,
            },
        );
        await flush();

        FakeWebSocket.instances[0].onclose?.({ code: 4401 });
        await settle();

        expect(FakeWebSocket.instances.length).toBe(1);
        expect(handle.closed).toBe(true);
        expect(onError.mock.calls[0][0].message).toMatch(
            /session refresh failed/,
        );
    });

    it.each([
        [4403, /no access to this pod's changes/],
        [4404, /pod or table not found/],
    ])(
        "stops for good on a %i close without retrying",
        async (code, message) => {
            const auth = makeAuth();
            const onError = vi.fn();
            const onStatus = vi.fn();
            const handle = watchDatastoreChanges(
                "https://api.x.test",
                auth,
                "POD",
                {
                    onChange: vi.fn(),
                    onError,
                    onStatus,
                },
            );
            await flush();

            FakeWebSocket.instances[0].onopen?.();
            FakeWebSocket.instances[0].onclose?.({ code });
            await settle();

            expect(FakeWebSocket.instances.length).toBe(1);
            expect(auth.refreshAccessToken).not.toHaveBeenCalled();
            expect(handle.closed).toBe(true);
            expect(onStatus).toHaveBeenLastCalledWith("closed");
            expect(onError.mock.calls[0][0].message).toMatch(message);
        },
    );

    it("treats other close codes, the socket limit included, as a dropped connection", async () => {
        const auth = makeAuth();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                onChange: vi.fn(),
            },
        );
        await flush();

        FakeWebSocket.instances[0].onclose?.({ code: 4429 });
        await settle();
        FakeWebSocket.instances[1].onclose?.({ code: 1008 });
        await settle();

        expect(auth.refreshAccessToken).not.toHaveBeenCalled();
        expect(FakeWebSocket.instances.length).toBe(3);
        handle.close();
    });

    it("keeps backing off when the server accepts and drops the socket before it is ready", async () => {
        vi.useFakeTimers();
        vi.spyOn(Math, "random").mockReturnValue(1); // delay = the full ceiling
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            {
                onChange: vi.fn(),
            },
        );
        await vi.advanceTimersByTimeAsync(0);

        const dropBeforeReady = () => {
            last().onopen?.();
            last().onclose?.({ code: 1006 });
        };

        dropBeforeReady();
        await vi.advanceTimersByTimeAsync(500);
        expect(FakeWebSocket.instances.length).toBe(2);

        /* Resetting on open made this 500 ms again, every time. */
        dropBeforeReady();
        await vi.advanceTimersByTimeAsync(999);
        expect(FakeWebSocket.instances.length).toBe(2);
        await vi.advanceTimersByTimeAsync(1);
        expect(FakeWebSocket.instances.length).toBe(3);

        /* A connection that got as far as `ready` was a real one. */
        last().receive({ type: "ready" });
        last().onclose?.({ code: 1006 });
        await vi.advanceTimersByTimeAsync(500);
        expect(FakeWebSocket.instances.length).toBe(4);

        handle.close();
    });

    it("gives up a watch after its maxRetries", async () => {
        const onError = vi.fn();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            {
                onChange: vi.fn(),
                onError,
                maxRetries: 1,
            },
        );
        await flush();
        last().onclose?.({ code: 1006 });
        await settle();
        last().onclose?.({ code: 1006 });
        await settle();
        expect(FakeWebSocket.instances.length).toBe(2);
        expect(handle.closed).toBe(true);
        expect(onError.mock.calls[0][0].message).toMatch(
            /max reconnect attempts/,
        );
    });

    it("stops for good once the session is signed out", async () => {
        const onError = vi.fn();
        const auth = {
            ...makeAuth(),
            getState: () => ({ status: "unauthenticated" }),
        };
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                onChange: vi.fn(),
                onError,
            },
        );
        await flush();

        FakeWebSocket.instances[0].onclose?.({ code: 1006 });
        await settle();

        expect(FakeWebSocket.instances.length).toBe(1);
        expect(handle.closed).toBe(true);
        expect(onError).toHaveBeenCalledWith(
            expect.objectContaining({
                message: expect.stringContaining("signed out"),
            }),
        );
    });

    it("does not reconnect after the last watch closed", async () => {
        vi.useFakeTimers();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            {
                onChange: vi.fn(),
            },
        );
        await vi.advanceTimersByTimeAsync(0);
        expect(FakeWebSocket.instances.length).toBe(1);
        handle.close();
        expect(handle.closed).toBe(true);
        await vi.advanceTimersByTimeAsync(1_000);
        expect(FakeWebSocket.instances[0].closed).toBe(true);

        // A late close event must not spawn a reconnect.
        FakeWebSocket.instances[0].onclose?.({ code: 1006 });
        await vi.advanceTimersByTimeAsync(1_000);
        expect(FakeWebSocket.instances.length).toBe(1);
    });

    it("does not put a token in the url in cookie mode", async () => {
        const auth = makeAuth();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            auth,
            "POD",
            {
                onChange: vi.fn(),
                useCookie: true,
            },
        );
        await flush();
        expect(auth.getAccessToken).not.toHaveBeenCalled();
        expect(FakeWebSocket.instances[0].url).toBe(
            "wss://api.x.test/pods/POD/live",
        );
        handle.close();
    });

    it("an aborted signal never subscribes", async () => {
        const controller = new AbortController();
        controller.abort();
        const handle = watchDatastoreChanges(
            "https://api.x.test",
            makeAuth(),
            "POD",
            {
                onChange: vi.fn(),
                signal: controller.signal,
            },
        );
        await flush();
        expect(handle.closed).toBe(true);
        expect(FakeWebSocket.instances.length).toBe(0);
    });
});

describe("the live socket url", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    // `lemma apps init --proxy` sets the API URL to "/api" in dev. The page
    // origin is stubbed in both rows: an absolute URL must not be rebased onto it.
    it.each([
        ["https://api.x.test", "wss://api.x.test/pods/POD/live?access_token=TOKEN"],
        ["/api", "ws://localhost:5173/api/pods/POD/live?access_token=TOKEN"],
    ])("is built for apiUrl %s against the page origin", async (apiUrl, expected) => {
        vi.stubGlobal("location", { origin: "http://localhost:5173" });
        const handle = watchDatastoreChanges(apiUrl, makeAuth(), "POD", {
            table: "notes",
            onChange: vi.fn(),
        });
        await flush();

        expect(FakeWebSocket.instances[0].url).toBe(expected);
        handle.close();
    });

    it.each([
        ["no location", undefined],
        ["an opaque origin", { origin: "null" }],
    ])("stops for good on a relative apiUrl with %s", async (_label, location) => {
        vi.stubGlobal("location", location);
        const onError = vi.fn();
        const onStatus = vi.fn();
        const handle = watchDatastoreChanges("/api", makeAuth(), "POD", {
            onChange: vi.fn(),
            onError,
            onStatus,
        });
        await flush();
        await flush();

        expect(FakeWebSocket.instances.length).toBe(0);
        expect(handle.closed).toBe(true);
        expect(onStatus).toHaveBeenLastCalledWith("closed");
        expect(onError).toHaveBeenCalledTimes(1);
        expect(onError.mock.calls[0][0].message).toMatch(/"\/api" is relative/);
    });

    it("stops for good on a malformed apiUrl instead of hanging in connecting", async () => {
        const onError = vi.fn();
        const onStatus = vi.fn();
        const handle = watchDatastoreChanges("https://[broken", makeAuth(), "POD", {
            onChange: vi.fn(),
            onError,
            onStatus,
        });
        await flush();
        await flush();

        expect(FakeWebSocket.instances.length).toBe(0);
        expect(handle.closed).toBe(true);
        expect(onStatus).toHaveBeenLastCalledWith("closed");
        expect(onError).toHaveBeenCalledTimes(1);
        expect(onError.mock.calls[0][0].message).toMatch(/"https:\/\/\[broken" is not a valid URL/);
    });
});
