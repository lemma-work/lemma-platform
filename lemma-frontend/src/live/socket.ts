/** The pod's one live socket, `/pods/{pod}/live`, as the web app holds it.
 *
 *  One connection per pod carries every subscription the app has open on it
 *  -- a channel's timeline, the inbox, a conversation's run, a table's rows --
 *  and the small calls answered on the same socket (typing, read, presence).
 *  `lemma-backend/docs/modules/live.md` is the protocol; this file is the
 *  client half of it, and nothing in it knows what a topic carries.
 *
 *  - Only `ready`, not the socket opening, says the connection is usable: the
 *    server accepts first and authenticates after, so a refusal is a close
 *    code this can read.
 *  - After every `ready` each subscription is sent again with the last cursor
 *    it was given, so a drop replays what was missed; a topic that cannot
 *    replay answers `resync`, and its owner refetches.
 *  - A drop reconnects with full-jitter backoff. 4401 refreshes the session
 *    once; 4403 and 4404 are final; 4429 (too many sockets) waits longer.
 *  - With no subscription and no call left, the socket closes after a short
 *    idle, so a remount reuses it rather than opening a second.
 *  - A socket can die without closing -- a laptop asleep, a network that
 *    changed under it -- and then no `onclose` ever comes. Each ping expects
 *    a frame back before the next is due; silence that long is a dead socket,
 *    dropped and dialled again. Coming back online, or to the tab, does not
 *    wait for that: a socket waiting to reconnect dials now, and an open one
 *    is pinged at once.
 *
 *  Pure of React and of the SDK: the WebSocket, the clock and the session are
 *  handed in, which is what lets the tests drive a reconnect frame by frame. */

export type LiveStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export interface WebSocketLike {
    send(text: string): void;
    close(code?: number, reason?: string): void;
    onmessage: ((event: { data: unknown }) => void) | null;
    onclose: ((event: { code: number }) => void) | null;
    onerror: ((event: unknown) => void) | null;
}

export interface LiveAuth {
    /** A bearer token to put in the URL, or null to rely on the cookie. */
    token(): Promise<string | null>;
    /** Refresh the session after a 4401; rejects when it cannot. */
    refresh(): Promise<void>;
}

export interface LiveClock {
    setTimeout(run: () => void, ms: number): unknown;
    clearTimeout(timer: unknown): void;
    setInterval(run: () => void, ms: number): unknown;
    clearInterval(timer: unknown): void;
}

export interface LiveSocketOptions {
    /** The socket's URL, with the token when there is one. */
    url(token: string | null): string;
    auth: LiveAuth;
    open(url: string): WebSocketLike;
    clock?: LiveClock;
    random?: () => number;
    /** How long a socket with nothing on it stays open. */
    idleCloseMs?: number;
    pingMs?: number;
    /** How long after a ping silence means the socket is dead; `pingMs` by
     *  default, so a dead socket is noticed within two pings of the last
     *  frame heard. */
    pongTimeoutMs?: number;
    callTimeoutMs?: number;
    /** Hear "worth checking now" -- back online, the tab in front again;
     *  returns how to stop listening. */
    wakeups?: (wake: () => void) => () => void;
}

export interface LiveHandlers {
    /** The subscription is live from `cursor` -- after every (re)connect. */
    onSubscribed?(cursor: string | null): void;
    /** A batch of the topic's events, in order; `changes` for `records`. */
    onEvents?(events: unknown[], cursor: string | null): void;
    /** Events were not or cannot be sent: refetch, then carry on. */
    onResync?(reason: string, cursor: string | null): void;
    /** The server ended it (`access_revoked`, `gone`, `table_dropped`) or
     *  refused it (`error` frame): it will not come back by itself. */
    onEnded?(reason: string, message?: string): void;
}

export interface LiveSubscription {
    readonly id: string;
    /** The last cursor the server gave this subscription. */
    readonly cursor: string | null;
    close(): void;
}

/** A call the socket could not answer: refused, or no socket to ask. */
export class LiveCallError extends Error {
    readonly code: string;

    constructor(code: string, message: string) {
        super(message);
        this.name = "LiveCallError";
        this.code = code;
    }
}

const WS_UNAUTHENTICATED = 4401;
const WS_FORBIDDEN = 4403;
const WS_NOT_FOUND = 4404;
const WS_TOO_MANY = 4429;
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 30_000;
/** Past the per-user socket limit another tab has to close first; asking
 *  again every half second only adds to the count. */
const TOO_MANY_DELAY_MS = 15_000;

/** Full jitter: anywhere from nothing to the doubled ceiling, so a server
 *  restart does not have every tab reconnecting in step. */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
    return random() * Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** Math.max(0, attempt));
}

/** `wss://api…/pods/{pod}/live`, with `?access_token=` when there is one --
 *  a browser cannot set a header on an upgrade. */
export function liveUrl(apiUrl: string, podId: string, token: string | null): string {
    const root = apiUrl.replace(/\/$/, "").replace(/^http(s?):\/\//, "ws$1://");
    const url = new URL(root + "/pods/" + encodeURIComponent(podId) + "/live");
    if (token) url.searchParams.set("access_token", token);
    return url.toString();
}

type Frame = Record<string, unknown>;

function cursorOf(frame: Frame): string | null {
    const cursor = frame.cursor ?? frame.stream_id;
    return typeof cursor === "string" && cursor ? cursor : null;
}

const realClock: LiveClock = {
    setTimeout: (run, ms) => setTimeout(run, ms),
    clearTimeout: (timer) => clearTimeout(timer as ReturnType<typeof setTimeout>),
    setInterval: (run, ms) => setInterval(run, ms),
    clearInterval: (timer) => clearInterval(timer as ReturnType<typeof setInterval>),
};

class Subscription implements LiveSubscription {
    cursor: string | null;
    live = true;
    readonly id: string;
    readonly topic: string;
    readonly params: Record<string, unknown>;
    readonly handlers: LiveHandlers;
    private readonly owner: LiveSocket;

    constructor(id: string, topic: string, params: Record<string, unknown>, handlers: LiveHandlers, since: string | null, owner: LiveSocket) {
        this.id = id;
        this.topic = topic;
        this.params = params;
        this.handlers = handlers;
        this.cursor = since;
        this.owner = owner;
    }

    frame(): string {
        return JSON.stringify({
            ...this.params,
            op: "subscribe",
            id: this.id,
            topic: this.topic,
            ...(this.cursor ? { since: this.cursor } : {}),
        });
    }

    close(): void {
        if (!this.live) return;
        this.live = false;
        this.owner.drop(this);
    }
}

interface PendingCall {
    resolve(value: unknown): void;
    reject(error: Error): void;
    timer: unknown;
}

export class LiveSocket {
    private socket: WebSocketLike | null = null;
    private ready = false;
    private opening = false;
    private attempt = 0;
    private refreshed = false;
    private final = false;
    private counter = 0;
    private reconnectTimer: unknown = null;
    private idleTimer: unknown = null;
    private pingTimer: unknown = null;
    private pongTimer: unknown = null;
    private readonly unwake: (() => void) | null;
    private current: LiveStatus = "idle";
    private readonly subscriptions = new Map<string, Subscription>();
    private readonly calls = new Map<string, PendingCall>();
    private readonly listeners = new Set<(status: LiveStatus) => void>();
    private readonly clock: LiveClock;
    private readonly random: () => number;
    private readonly options: LiveSocketOptions;

    constructor(options: LiveSocketOptions) {
        this.options = options;
        this.clock = options.clock ?? realClock;
        this.random = options.random ?? Math.random;
        this.unwake = options.wakeups?.(() => this.wake()) ?? null;
    }

    /** Something says the network may be back, or may have gone: a socket
     *  waiting out its backoff dials now; an open one is asked to answer. */
    wake(): void {
        if (this.final) return;
        if (this.reconnectTimer !== null) {
            this.clock.clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
            if (this.wanted()) void this.connect();
            else this.setStatus("idle");
            return;
        }
        if (this.ready) this.ping();
    }

    get status(): LiveStatus {
        return this.current;
    }

    /** Hear the connection's status; called at once with the current one. */
    onStatus(listener: (status: LiveStatus) => void): () => void {
        this.listeners.add(listener);
        listener(this.current);
        return () => { this.listeners.delete(listener); };
    }

    subscribe(topic: string, params: Record<string, unknown>, handlers: LiveHandlers, since: string | null = null): LiveSubscription {
        this.counter += 1;
        const subscription = new Subscription(topic.slice(0, 1) + this.counter, topic, params, handlers, since, this);
        if (this.final) {
            subscription.live = false;
            handlers.onEnded?.("closed", "The live connection was refused.");
            return subscription;
        }
        this.subscriptions.set(subscription.id, subscription);
        this.stopIdle();
        if (this.ready) this.send(subscription.frame());
        else this.ensureOpen();
        return subscription;
    }

    /** One call, answered on the socket. Refused at once when there is no
     *  usable socket: every call is ephemeral, and its caller has a REST
     *  answer or none worth waiting for. */
    call(method: string, params: Record<string, unknown>): Promise<unknown> {
        if (!this.ready) return Promise.reject(new LiveCallError("unavailable", "The live connection is not open."));
        this.counter += 1;
        const id = "k" + this.counter;
        return new Promise((resolve, reject) => {
            const timer = this.clock.setTimeout(() => {
                this.calls.delete(id);
                reject(new LiveCallError("unavailable", "The live connection did not answer."));
            }, this.options.callTimeoutMs ?? 10_000);
            this.calls.set(id, { resolve, reject, timer });
            this.send(JSON.stringify({ op: "call", id, method, params }));
        });
    }

    /** Stop for good: the page is going. */
    close(): void {
        this.final = true;
        this.unwake?.();
        for (const subscription of this.subscriptions.values()) subscription.live = false;
        this.subscriptions.clear();
        this.teardown();
        this.setStatus("closed");
    }

    /** @internal -- a subscription closed by its owner. */
    drop(subscription: Subscription): void {
        if (!this.subscriptions.delete(subscription.id)) return;
        if (this.ready) this.send(JSON.stringify({ op: "unsubscribe", id: subscription.id }));
        this.maybeIdle();
    }

    private setStatus(next: LiveStatus): void {
        if (this.current === next) return;
        this.current = next;
        for (const listener of [...this.listeners]) listener(next);
    }

    private send(text: string): void {
        try {
            this.socket?.send(text);
        } catch {
            /* Lost on a closing socket; the reconnect sends every subscription again. */
        }
    }

    private ensureOpen(): void {
        if (this.socket || this.opening || this.reconnectTimer !== null || this.final) return;
        void this.connect();
    }

    private async connect(): Promise<void> {
        this.opening = true;
        this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");
        let token: string | null = null;
        try {
            token = await this.options.auth.token();
        } catch {
            token = null;
        }
        this.opening = false;
        if (this.final || !this.wanted()) { this.setStatus("idle"); return; }
        let socket: WebSocketLike;
        try {
            socket = this.options.open(this.options.url(token));
        } catch {
            this.retry(false);
            return;
        }
        this.socket = socket;
        socket.onmessage = (event) => { if (this.socket === socket) this.receive(event.data); };
        socket.onclose = (event) => { if (this.socket === socket) this.closed(event.code); };
        socket.onerror = () => { /* onclose follows */ };
    }

    private wanted(): boolean {
        return this.subscriptions.size > 0 || this.calls.size > 0;
    }

    private receive(data: unknown): void {
        this.heard();
        let frame: Frame;
        try {
            const parsed: unknown = JSON.parse(typeof data === "string" ? data : "");
            if (!parsed || typeof parsed !== "object") return;
            frame = parsed as Frame;
        } catch {
            return;
        }
        if (frame.type === "ready") {
            this.ready = true;
            this.attempt = 0;
            this.refreshed = false;
            this.setStatus("open");
            for (const subscription of this.subscriptions.values()) this.send(subscription.frame());
            this.startPing();
            return;
        }
        if (frame.type === "pong") return;
        const id = typeof frame.id === "string" ? frame.id : null;
        if (frame.type === "result" || (frame.type === "error" && id && this.calls.has(id))) {
            const pending = id ? this.calls.get(id) : undefined;
            if (!pending || !id) return;
            this.calls.delete(id);
            this.clock.clearTimeout(pending.timer);
            if (frame.type === "result") pending.resolve(frame.result ?? null);
            else pending.reject(new LiveCallError(String(frame.code ?? "invalid"), String(frame.message ?? "")));
            this.maybeIdle();
            return;
        }
        const subscription = id ? this.subscriptions.get(id) : undefined;
        if (!subscription) return;
        const cursor = cursorOf(frame);
        switch (frame.type) {
            case "subscribed":
                subscription.cursor = cursor ?? subscription.cursor;
                subscription.handlers.onSubscribed?.(subscription.cursor);
                return;
            case "events":
            case "changes": {
                /* Only the last frame of a split batch moves the cursor; the
                   others carry the one before it, so a drop mid-batch replays
                   the whole batch. */
                subscription.cursor = cursor ?? subscription.cursor;
                const list = frame.type === "events" ? frame.events : frame.changes;
                subscription.handlers.onEvents?.(Array.isArray(list) ? list : [], subscription.cursor);
                return;
            }
            case "resync":
                subscription.cursor = cursor ?? subscription.cursor;
                subscription.handlers.onResync?.(typeof frame.reason === "string" ? frame.reason : "unknown", subscription.cursor);
                return;
            case "unsubscribed":
                if (frame.reason === "client") return;
                this.end(subscription, String(frame.reason ?? "gone"));
                return;
            case "error":
                this.end(subscription, String(frame.code ?? "invalid"), typeof frame.message === "string" ? frame.message : undefined);
                return;
            default:
                return;
        }
    }

    private end(subscription: Subscription, reason: string, message?: string): void {
        this.subscriptions.delete(subscription.id);
        subscription.live = false;
        subscription.handlers.onEnded?.(reason, message);
        this.maybeIdle();
    }

    private closed(code: number): void {
        this.socket = null;
        this.ready = false;
        this.stopPing();
        this.failCalls();
        if (this.final) return;
        if (!this.wanted()) { this.setStatus("idle"); return; }
        if (code === WS_UNAUTHENTICATED) {
            if (this.refreshed) { this.giveUp("unauthenticated"); return; }
            this.refreshed = true;
            this.setStatus("reconnecting");
            this.options.auth.refresh().then(() => this.retry(false), () => this.giveUp("unauthenticated"));
            return;
        }
        if (code === WS_FORBIDDEN) { this.giveUp("forbidden"); return; }
        if (code === WS_NOT_FOUND) { this.giveUp("not_found"); return; }
        this.retry(code === WS_TOO_MANY);
    }

    private retry(tooMany: boolean): void {
        if (this.final) return;
        this.setStatus("reconnecting");
        const delay = tooMany ? Math.max(TOO_MANY_DELAY_MS, reconnectDelay(this.attempt, this.random)) : reconnectDelay(this.attempt, this.random);
        this.attempt += 1;
        this.reconnectTimer = this.clock.setTimeout(() => {
            this.reconnectTimer = null;
            if (this.wanted()) void this.connect();
            else this.setStatus("idle");
        }, delay);
    }

    /** Nothing a retry can fix: say so to every subscription and stop. */
    private giveUp(reason: string): void {
        this.final = true;
        const ended = [...this.subscriptions.values()];
        this.subscriptions.clear();
        this.setStatus("closed");
        for (const subscription of ended) {
            subscription.live = false;
            subscription.handlers.onEnded?.(reason);
        }
    }

    private failCalls(): void {
        for (const [id, pending] of this.calls) {
            this.clock.clearTimeout(pending.timer);
            pending.reject(new LiveCallError("unavailable", "The live connection closed."));
            this.calls.delete(id);
        }
    }

    private maybeIdle(): void {
        if (this.wanted() || this.idleTimer !== null) return;
        this.idleTimer = this.clock.setTimeout(() => {
            this.idleTimer = null;
            if (!this.wanted()) { this.teardown(); this.setStatus("idle"); }
        }, this.options.idleCloseMs ?? 1_500);
    }

    private stopIdle(): void {
        if (this.idleTimer === null) return;
        this.clock.clearTimeout(this.idleTimer);
        this.idleTimer = null;
    }

    private startPing(): void {
        this.stopPing();
        this.pingTimer = this.clock.setInterval(() => this.ping(), this.options.pingMs ?? 25_000);
    }

    /** A ping that expects an answer: anything heard before the deadline will
     *  do, and the first deadline armed stands. */
    private ping(): void {
        this.send('{"op":"ping"}');
        if (this.pongTimer !== null) return;
        this.pongTimer = this.clock.setTimeout(() => {
            this.pongTimer = null;
            this.dead();
        }, this.options.pongTimeoutMs ?? this.options.pingMs ?? 25_000);
    }

    private heard(): void {
        if (this.pongTimer === null) return;
        this.clock.clearTimeout(this.pongTimer);
        this.pongTimer = null;
    }

    /** A socket gone silent: no close is coming, so it is closed here and
     *  dialled again as after any drop. */
    private dead(): void {
        const socket = this.socket;
        if (!socket) return;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        try {
            socket.close(4000, "no answer");
        } catch {
            /* already gone */
        }
        this.closed(1006);
    }

    private stopPing(): void {
        if (this.pingTimer !== null) this.clock.clearInterval(this.pingTimer);
        this.pingTimer = null;
        this.heard();
    }

    private teardown(): void {
        this.stopPing();
        this.stopIdle();
        if (this.reconnectTimer !== null) this.clock.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
        this.failCalls();
        const socket = this.socket;
        this.socket = null;
        this.ready = false;
        this.attempt = 0;
        if (socket) {
            try {
                socket.close(1000, "done");
            } catch {
                /* already closing */
            }
        }
    }
}
