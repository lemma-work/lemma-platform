/**
 * Live datastore changes over the pod's live socket.
 *
 * Every `watchChanges` / `useLiveRecords` in a page shares **one** WebSocket per
 * `(apiUrl, podId)`: `/pods/{podId}/live`. Each watch is a subscription on it,
 * named by an id this module picks, and the socket is ref-counted — it opens
 * with the first subscription and closes shortly after the last one goes.
 *
 * Protocol (JSON frames):
 *
 *   → {"op":"subscribe","id":"s1","topic":"records","table":"tickets","since":"<cursor>"}
 *   → {"op":"unsubscribe","id":"s1"}        → {"op":"ping"}
 *   ← {"type":"ready","connection":"…"}     ← {"type":"subscribed","id":"s1","cursor":"…"}
 *   ← {"type":"changes","id":"s1","cursor":"…","changes":[{"op","table","record_id","row"}]}
 *   ← {"type":"resync","id":"s1","cursor":"…","reason":"…"}   refetch, then continue
 *   ← {"type":"error","id":"s1","code":"…"} ← {"type":"unsubscribed","id":"s1","reason":"…"}
 *
 * RLS (per-user) tables deliver only the caller's own rows; shared tables
 * deliver every member's changes — the server enforces this, the client renders.
 *
 * The browser WebSocket API cannot set request headers, so auth is carried as an
 * `?access_token=` query parameter (resolved from the session). Same-site cookie
 * sessions also work if you pass `useCookie: true` and omit the token.
 *
 * Reconnects with full-jitter backoff and resubscribes every subscription from
 * its own last `cursor`, so a brief drop replays missed changes rather than
 * losing them. When the server cannot replay (the gap is too old or too large)
 * it sends `resync` instead: the subscription's `onResync` is called and the
 * caller refetches its view.
 *
 * A subscription the server could not start just now arrives as an `error`
 * frame with code `unavailable`. That one subscription is re-sent from its
 * cursor after its own full-jitter backoff (reset by `subscribed`); the socket
 * stays up and the watch only reports `reconnecting` meanwhile. Every other
 * `error` code ends the subscription through `onError`.
 *
 * A rejected session arrives as close code 4401 (the server accepts first, so
 * the code survives — a pre-accept close would reach a browser as 1006). The
 * connection refreshes the session once and reconnects; a second 4401 before
 * the socket is ready again is terminal and reported through `onError`.
 */

export interface DatastoreChangeFrame {
    /** e.g. "datastore.record.insert" | ".update" | ".delete" */
    type: string;
    pod_id: string;
    table_name: string;
    record_id: string;
    operation: "insert" | "update" | "delete";
    /**
     * The row as it stands now — including columns this write never touched and
     * defaults the database filled in. Several changes to one record close
     * together arrive as one frame carrying its latest state. `{}` on `delete`.
     */
    payload: Record<string, unknown>;
    /**
     * @deprecated Always `false`: every frame carries the whole row. Kept so
     * code that reads it still compiles.
     */
    payload_truncated?: boolean;
    occurred_at?: string;
    /** Change-log cursor — pass back as `since` to resume after this change. */
    cursor?: string;
    /** @deprecated Alias of `cursor`, kept for one release. */
    stream_id?: string;
}

export type ChangeStreamStatus =
    "connecting" | "open" | "reconnecting" | "closed";

/** Why the server asked a subscription to refetch instead of replaying. */
export type ChangeStreamResyncReason =
    | "cursor_expired"
    | "too_many"
    | "permission_changed"
    | "overflow"
    | (string & {});

/** Minimal token source; the SDK's AuthManager satisfies this structurally. */
export interface ChangeStreamTokenProvider {
    getAccessToken(): Promise<string>;
    refreshAccessToken(): Promise<string>;
    /**
     * The session's state, when the provider knows it. A signed-out session ends
     * the stream: the server refuses the handshake before accepting it, which a
     * browser reports as 1006 rather than 1008, so the close code alone cannot
     * say "not you" and the socket would otherwise retry that refusal forever.
     */
    getState?(): { status: string };
}

export interface WatchChangesOptions {
    /** Restrict to one table; omit to watch every readable table in the pod. */
    table?: string;
    /** Only these operations; omit for all three. */
    ops?: Array<"insert" | "update" | "delete">;
    /**
     * Only rows whose current values match, in the trigger `when` grammar's value
     * operators: `{ status: "open" }`, `{ status: { in: ["open", "new"] } }`,
     * `equals` / `not_equals` / `in` / `not_in`. Needs `table`. Deletes are always
     * delivered; a row updated out of the filter is not.
     */
    when?: Record<string, unknown>;
    /** Resume after a previously seen `cursor` (or legacy `stream_id`). */
    since?: string;
    /** Invoked for every record change. */
    onChange: (frame: DatastoreChangeFrame) => void;
    /**
     * The server could not replay what this watch missed — the gap was older than
     * the server keeps, or larger than is worth sending. Refetch your view, then
     * keep applying changes: they continue from `cursor`.
     */
    onResync?: (info: {
        cursor: string;
        reason: ChangeStreamResyncReason;
    }) => void;
    /** Invoked each time the subscription is live; carries the resume cursor. */
    onReady?: (info: { since: string }) => void;
    /** Connection lifecycle notifications. */
    onStatus?: (status: ChangeStreamStatus) => void;
    /** Terminal errors (e.g. auth failed, access revoked, max retries reached). */
    onError?: (error: Error) => void;
    /** Abort to stop the stream (equivalent to calling `handle.close()`). */
    signal?: AbortSignal;
    /** Rely on a same-site session cookie instead of a token in the URL. */
    useCookie?: boolean;
    /** Cap reconnect attempts; unlimited by default. */
    maxRetries?: number;
}

export interface ChangeStreamHandle {
    /** Stop the stream and prevent further reconnects. */
    close(): void;
    /** Whether the stream has been stopped. */
    readonly closed: boolean;
}

const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 30_000;
/** A socket with no subscriptions closes after this, so a remount reuses it. */
const IDLE_CLOSE_MS = 1_000;
/** Keepalive, under the idle timeout of common proxies. */
const PING_INTERVAL_MS = 25_000;
/** Close code for a missing, invalid or expired session: refresh and retry. */
const WS_UNAUTHENTICATED = 4401;
/** Close codes no retry can fix: the caller lacks access, or the pod is gone. */
const WS_FORBIDDEN = 4403;
const WS_NOT_FOUND = 4404;
/** Error code for "could not decide just now; retry later" — not terminal. */
const ERROR_UNAVAILABLE = "unavailable";

function reconnectDelayMs(attempt: number): number {
    const ceiling = Math.min(
        RECONNECT_MAX_DELAY_MS,
        RECONNECT_BASE_DELAY_MS * 2 ** Math.max(0, attempt),
    );
    return Math.random() * ceiling;
}

/** A relative `apiUrl` with no page to resolve it against. No retry fixes it. */
class UnresolvableApiUrl extends Error {}

/**
 * `apiUrl` as an absolute URL. A relative one ("/api", which `lemma apps init
 * --proxy` writes for the same-origin Vite dev proxy) works for `fetch`, which
 * resolves against the page, but a WebSocket URL has to be built whole, so it
 * is resolved against the page origin here.
 */
function absoluteApiUrl(apiUrl: string): string {
    if (/^[a-z][a-z\d+.-]*:/i.test(apiUrl)) return apiUrl;
    const origin = globalThis.location?.origin;
    // An opaque origin (about:blank, file://) serialises as the string "null".
    if (!origin || origin === "null") {
        throw new UnresolvableApiUrl(
            `Datastore change stream: apiUrl "${apiUrl}" is relative and there is no page origin to resolve it against; pass an absolute URL`,
        );
    }
    return new URL(apiUrl, origin).toString();
}

function liveWsUrl(
    apiUrl: string,
    podId: string,
    token: string | null,
): string {
    const root = absoluteApiUrl(apiUrl)
        .replace(/\/$/, "")
        .replace(/^http(s?):\/\//, "ws$1://");
    const url = new URL(`${root}/pods/${podId}/live`);
    if (token) url.searchParams.set("access_token", token);
    return url.toString();
}

interface WireChange {
    op: "insert" | "update" | "delete";
    table: string;
    record_id: string;
    row: Record<string, unknown> | null;
}

type ServerFrame = Record<string, unknown> & { type?: unknown; id?: unknown };

function frameCursor(frame: ServerFrame): string | undefined {
    const cursor = frame.cursor ?? frame.stream_id;
    return typeof cursor === "string" && cursor ? cursor : undefined;
}

class Subscription {
    status: ChangeStreamStatus | null = null;
    stopped = false;
    cursor: string | undefined;
    /** Consecutive `unavailable` refusals since the last `subscribed`. */
    retryAttempt = 0;
    retryTimer: ReturnType<typeof setTimeout> | null = null;

    constructor(
        readonly id: string,
        readonly options: WatchChangesOptions,
    ) {
        this.cursor = options.since;
    }

    setStatus(next: ChangeStreamStatus): void {
        if (this.status === next) return;
        this.status = next;
        this.options.onStatus?.(next);
    }

    cancelRetry(): void {
        if (this.retryTimer != null) clearTimeout(this.retryTimer);
        this.retryTimer = null;
    }

    subscribeFrame(): string {
        const frame: Record<string, unknown> = {
            op: "subscribe",
            id: this.id,
            topic: "records",
        };
        if (this.options.table) frame.table = this.options.table;
        if (this.options.ops?.length) frame.ops = this.options.ops;
        if (this.options.when) frame.when = this.options.when;
        if (this.cursor) frame.since = this.cursor;
        return JSON.stringify(frame);
    }
}

/**
 * One socket per (auth, apiUrl, podId), shared by every subscription on it.
 * Exported for tests; use `watchDatastoreChanges`.
 */
export class LiveConnection {
    private socket: WebSocket | null = null;
    private ready = false;
    private attempt = 0;
    private authRefreshed = false;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private idleTimer: ReturnType<typeof setTimeout> | null = null;
    private pingTimer: ReturnType<typeof setInterval> | null = null;
    private connecting = false;
    private nextId = 1;
    private closed = false;
    readonly subscriptions = new Map<string, Subscription>();

    constructor(
        private readonly apiUrl: string,
        private readonly auth: ChangeStreamTokenProvider,
        private readonly podId: string,
        private readonly useCookie: boolean,
        private readonly onDispose: (connection: LiveConnection) => void,
    ) {}

    get isClosed(): boolean {
        return this.closed;
    }

    add(options: WatchChangesOptions): Subscription {
        if (this.idleTimer != null) {
            clearTimeout(this.idleTimer);
            this.idleTimer = null;
        }
        const subscription = new Subscription(`s${this.nextId++}`, options);
        this.subscriptions.set(subscription.id, subscription);
        subscription.setStatus(
            this.attempt === 0 ? "connecting" : "reconnecting",
        );
        if (this.ready) this.send(subscription.subscribeFrame());
        else this.ensureConnecting();
        return subscription;
    }

    remove(subscription: Subscription): void {
        subscription.cancelRetry();
        if (!this.subscriptions.delete(subscription.id)) return;
        if (this.ready)
            this.send(
                JSON.stringify({ op: "unsubscribe", id: subscription.id }),
            );
        if (this.subscriptions.size === 0 && this.idleTimer == null) {
            this.idleTimer = setTimeout(() => {
                this.idleTimer = null;
                if (this.subscriptions.size === 0) this.dispose();
            }, IDLE_CLOSE_MS);
        }
    }

    private send(text: string): void {
        try {
            this.socket?.send(text);
        } catch {
            // A send on a closing socket is lost; onclose resubscribes everything.
        }
    }

    private ensureConnecting(): void {
        if (
            this.socket ||
            this.connecting ||
            this.reconnectTimer != null ||
            this.closed
        )
            return;
        void this.connect();
    }

    private async connect(): Promise<void> {
        if (this.closed) return;
        this.connecting = true;
        let token: string | null = null;
        if (!this.useCookie) {
            try {
                token = await this.auth.getAccessToken();
            } catch {
                token = null; // fall back to cookie auth
            }
        }
        this.connecting = false;
        if (this.closed || this.subscriptions.size === 0) return;

        let url: string;
        try {
            url = liveWsUrl(this.apiUrl, this.podId, token);
        } catch (error) {
            // Said once and stopped: apiUrl does not change, so retrying could
            // only fail the same way -- relative with no origin, or malformed.
            this.failAll(
                error instanceof UnresolvableApiUrl
                    ? error
                    : new Error(
                          `Datastore change stream: apiUrl "${this.apiUrl}" is not a valid URL`,
                          { cause: error },
                      ),
            );
            return;
        }
        let ws: WebSocket;
        try {
            ws = new WebSocket(url);
        } catch (error) {
            for (const sub of this.subscriptions.values()) {
                sub.options.onError?.(
                    error instanceof Error ? error : new Error(String(error)),
                );
            }
            this.scheduleReconnect();
            return;
        }
        this.socket = ws;
        ws.onmessage = (event: MessageEvent) => this.onMessage(event);
        ws.onclose = (event: CloseEvent) => {
            if (this.socket === ws) this.onClose(event.code);
        };
        // onerror is followed by onclose, where reconnect is handled.
        ws.onerror = () => {};
    }

    private onMessage(event: MessageEvent): void {
        let frame: ServerFrame;
        try {
            const parsed: unknown = JSON.parse(
                typeof event.data === "string" ? event.data : "",
            );
            if (!parsed || typeof parsed !== "object") return;
            frame = parsed as ServerFrame;
        } catch {
            return;
        }
        if (frame.type === "ready") {
            // Not `onopen`: the server accepts before it authenticates, so only
            // `ready` says the session was taken. Backoff resets here too, so a
            // server that accepts and then drops the socket is not hammered.
            this.ready = true;
            this.attempt = 0;
            this.authRefreshed = false;
            for (const sub of this.subscriptions.values()) {
                sub.cancelRetry(); // re-sent now; a pending retry would duplicate it
                this.send(sub.subscribeFrame());
            }
            this.startPing();
            return;
        }
        const sub =
            typeof frame.id === "string"
                ? this.subscriptions.get(frame.id)
                : undefined;
        if (!sub) return;
        const cursor = frameCursor(frame);
        switch (frame.type) {
            case "subscribed":
                sub.cursor = cursor ?? sub.cursor;
                sub.retryAttempt = 0;
                sub.setStatus("open");
                if (sub.cursor) sub.options.onReady?.({ since: sub.cursor });
                return;
            case "changes": {
                sub.cursor = cursor ?? sub.cursor;
                const changes = Array.isArray(frame.changes)
                    ? (frame.changes as WireChange[])
                    : [];
                for (const change of changes)
                    sub.options.onChange(this.toFrame(change, sub.cursor));
                return;
            }
            case "resync":
                sub.cursor = cursor ?? sub.cursor;
                sub.options.onResync?.({
                    cursor: sub.cursor ?? "",
                    reason:
                        typeof frame.reason === "string"
                            ? frame.reason
                            : "unknown",
                });
                return;
            case "error":
                if (frame.code === ERROR_UNAVAILABLE) {
                    this.retrySubscribe(sub);
                    return;
                }
                this.end(
                    sub,
                    `subscription refused (${String(frame.code)}): ${String(frame.message ?? "")}`,
                );
                return;
            case "unsubscribed":
                if (frame.reason === "client") return;
                this.end(
                    sub,
                    `subscription ended by the server (${String(frame.reason)})`,
                );
                return;
            default:
                return;
        }
    }

    private toFrame(
        change: WireChange,
        cursor: string | undefined,
    ): DatastoreChangeFrame {
        return {
            type: `datastore.record.${change.op}`,
            pod_id: this.podId,
            table_name: change.table,
            record_id: change.record_id,
            operation: change.op,
            payload: change.row ?? {},
            cursor,
            stream_id: cursor,
        };
    }

    /**
     * Re-send one subscription the server could not start just now, after its
     * own backoff, on the same socket. A reconnect in the meantime cancels the
     * timer: `ready` re-sends every subscription anyway.
     */
    private retrySubscribe(sub: Subscription): void {
        sub.cancelRetry();
        const max = sub.options.maxRetries;
        if (max != null && sub.retryAttempt >= max) {
            this.end(sub, "max retry attempts reached (unavailable)");
            return;
        }
        sub.setStatus("reconnecting");
        const delay = reconnectDelayMs(sub.retryAttempt);
        sub.retryAttempt += 1;
        sub.retryTimer = setTimeout(() => {
            sub.retryTimer = null;
            if (
                this.closed ||
                !this.ready ||
                this.subscriptions.get(sub.id) !== sub
            )
                return;
            this.send(sub.subscribeFrame());
        }, delay);
    }

    private end(sub: Subscription, message: string): void {
        sub.cancelRetry();
        this.subscriptions.delete(sub.id);
        sub.stopped = true;
        sub.setStatus("closed");
        sub.options.onError?.(new Error(`Datastore change stream: ${message}`));
        if (this.subscriptions.size === 0) this.dispose();
    }

    private failAll(error: Error): void {
        for (const sub of [...this.subscriptions.values()]) {
            sub.cancelRetry();
            this.subscriptions.delete(sub.id);
            sub.stopped = true;
            sub.setStatus("closed");
            sub.options.onError?.(error);
        }
        this.dispose();
    }

    private onClose(code: number): void {
        this.socket = null;
        this.ready = false;
        this.stopPing();
        for (const sub of this.subscriptions.values()) sub.cancelRetry();
        if (this.closed) return;
        if (this.subscriptions.size === 0) {
            this.dispose();
            return;
        }
        if (code === WS_UNAUTHENTICATED) {
            if (this.authRefreshed) {
                this.failAll(
                    new Error(
                        "Datastore change stream: session rejected after refresh",
                    ),
                );
                return;
            }
            this.authRefreshed = true;
            this.auth.refreshAccessToken().then(
                () => this.scheduleReconnect(),
                (error: unknown) =>
                    this.failAll(
                        new Error(
                            `Datastore change stream: session refresh failed (${
                                error instanceof Error
                                    ? error.message
                                    : String(error)
                            })`,
                        ),
                    ),
            );
            return;
        }
        if (code === WS_FORBIDDEN) {
            this.failAll(
                new Error(
                    "Datastore change stream: no access to this pod's changes",
                ),
            );
            return;
        }
        if (code === WS_NOT_FOUND) {
            this.failAll(
                new Error("Datastore change stream: pod or table not found"),
            );
            return;
        }
        this.scheduleReconnect();
    }

    private scheduleReconnect(): void {
        if (this.closed) return;
        if (this.auth.getState?.().status === "unauthenticated") {
            this.failAll(new Error("Datastore change stream: signed out"));
            return;
        }
        for (const sub of [...this.subscriptions.values()]) {
            const max = sub.options.maxRetries;
            if (max != null && this.attempt >= max) {
                this.end(sub, "max reconnect attempts reached");
            }
        }
        if (this.closed || this.subscriptions.size === 0) return;
        for (const sub of this.subscriptions.values())
            sub.setStatus("reconnecting");
        const delay = reconnectDelayMs(this.attempt);
        this.attempt += 1;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            void this.connect();
        }, delay);
    }

    private startPing(): void {
        this.stopPing();
        this.pingTimer = setInterval(
            () => this.send('{"op":"ping"}'),
            PING_INTERVAL_MS,
        );
    }

    private stopPing(): void {
        if (this.pingTimer != null) clearInterval(this.pingTimer);
        this.pingTimer = null;
    }

    dispose(): void {
        if (this.closed) return;
        this.closed = true;
        this.stopPing();
        for (const sub of this.subscriptions.values()) sub.cancelRetry();
        if (this.reconnectTimer != null) clearTimeout(this.reconnectTimer);
        if (this.idleTimer != null) clearTimeout(this.idleTimer);
        this.reconnectTimer = null;
        this.idleTimer = null;
        const socket = this.socket;
        this.socket = null;
        this.ready = false;
        if (socket) {
            try {
                socket.close(1000, "client closed");
            } catch {
                // ignore
            }
        }
        this.onDispose(this);
    }
}

const connections = new WeakMap<
    ChangeStreamTokenProvider,
    Map<string, LiveConnection>
>();

function sharedConnection(
    apiUrl: string,
    auth: ChangeStreamTokenProvider,
    podId: string,
    useCookie: boolean,
): LiveConnection {
    let byKey = connections.get(auth);
    if (!byKey) {
        byKey = new Map();
        connections.set(auth, byKey);
    }
    const key = `${apiUrl.replace(/\/$/, "")}|${podId}|${useCookie ? "cookie" : "token"}`;
    const existing = byKey.get(key);
    if (existing && !existing.isClosed) return existing;
    const map = byKey;
    const connection = new LiveConnection(
        apiUrl,
        auth,
        podId,
        useCookie,
        (gone) => {
            if (map.get(key) === gone) map.delete(key);
        },
    );
    map.set(key, connection);
    return connection;
}

/**
 * Watch live datastore changes. Returns a handle; call `close()` (or abort the
 * provided `signal`) to stop. Watches on the same pod share one socket.
 */
export function watchDatastoreChanges(
    apiUrl: string,
    auth: ChangeStreamTokenProvider,
    podId: string,
    options: WatchChangesOptions,
): ChangeStreamHandle {
    let subscription: Subscription | null = null;
    let connection: LiveConnection | null = null;
    let stopped = false;

    const close = (): void => {
        if (stopped) return;
        stopped = true;
        if (subscription && connection && !subscription.stopped) {
            subscription.stopped = true;
            connection.remove(subscription);
            subscription.setStatus("closed");
        }
    };

    if (options.signal?.aborted) {
        stopped = true;
    } else {
        options.signal?.addEventListener("abort", close, { once: true });
        connection = sharedConnection(
            apiUrl,
            auth,
            podId,
            Boolean(options.useCookie),
        );
        subscription = connection.add(options);
    }

    return {
        close,
        get closed() {
            return stopped || Boolean(subscription?.stopped);
        },
    };
}
