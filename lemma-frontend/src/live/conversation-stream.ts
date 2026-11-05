import type { LiveHandlers, LiveStatus, LiveSubscription } from "./socket";

/** A conversation's run, read off the pod's live socket instead of its SSE
 *  stream -- as the same SSE bytes, so the SDK's session (`consume`, and every
 *  rule it keeps about reconnecting, catching up and finishing a turn) reads
 *  it unchanged.
 *
 *  The `conversation` topic carries exactly what the SSE endpoints publish
 *  (`token`, `message`, `status`, `completed`, `error`, `stopped`, `title` …,
 *  each with its `agent_run_id`), and watches a run the way they do: right
 *  after `subscribed` it says which run it watches (`run`: the one named, else
 *  the one going, else none), replays that run's ending if it is over, and
 *  re-reads it through a long silence. What is left here:
 *
 *  - **Send.** `POST …/messages` streams the run it started; here the send is
 *    `POST …/messages/append` -- the same `send_from_lemma`, unstreamed --
 *    made once the subscription is listening, and its answer becomes the
 *    stream's first `status` frame, as the SSE route's is.
 *  - **One run.** Events of another run are left out; conversation-scoped
 *    ones (a new title) pass. The `run` frame is the socket's, not SSE's.
 *  - **The end.** `completed`, `stopped` or `error` ends the stream; so does
 *    "no run" when attaching. A socket that drops or falls behind (`resync`)
 *    ends it without a terminal frame, which is what sends the session to
 *    read the conversation over REST and attach again.
 *
 *  Pure of React and of the SDK; the tests drive it with a fake socket. */

export interface LiveTopicSource {
    readonly status: LiveStatus;
    subscribe(topic: string, params: Record<string, unknown>, handlers: LiveHandlers): LiveSubscription;
    onStatus(listener: (status: LiveStatus) => void): () => void;
}

export interface RunStart {
    agent_run_id: string;
    started_new_run?: boolean;
    deduplicated?: boolean;
}

export interface RunStreamDeps {
    live: LiveTopicSource;
    /** The conversation's status now, over REST: asked only when a send
     *  joined a run that may have ended before anybody listened. */
    status(conversationId: string): Promise<string | null | undefined>;
    /** Send without a stream: `POST …/messages/append`. */
    append(conversationId: string, payload: unknown): Promise<RunStart>;
    /** How long to wait for the socket before the caller falls back to SSE. */
    subscribeMs?: number;
    setTimeout?: (run: () => void, ms: number) => unknown;
    clearTimeout?: (timer: unknown) => void;
}

/** The socket could not carry this run: use the SSE stream. */
export class SocketUnavailable extends Error {
    constructor(message: string) {
        super(message);
        this.name = "SocketUnavailable";
    }
}

const TERMINAL = new Set(["completed", "stopped", "error"]);

export function isRunning(status: string | null | undefined): boolean {
    const normal = (status ?? "").toUpperCase();
    return normal === "RUNNING" || normal === "STOP_REQUESTED";
}

/** One event as the SSE endpoint writes it (`encode_stream_chunk`): a token
 *  keeps its `kind` and drops the run id; everything else carries it. */
export function sseFrame(event: Record<string, unknown>): string {
    const type = String(event.type ?? "");
    const payload: Record<string, unknown> = { type, data: event.data ?? null };
    if (type === "token") {
        if (event.kind) payload.kind = event.kind;
    } else {
        payload.agent_run_id = typeof event.agent_run_id === "string" ? event.agent_run_id : null;
    }
    return "data: " + JSON.stringify(payload) + "\n\n";
}

/** Whether the socket is worth waiting on for a run. A socket going --
 *  `idle` until something subscribes, `connecting`, `open` -- is; one that
 *  dropped and is waiting out its backoff, or was refused for good, is not:
 *  a send waiting the whole subscribe timeout on it only to fall back to SSE
 *  is a send held for nothing. */
export function socketCarries(status: LiveStatus): boolean {
    return status !== "reconnecting" && status !== "closed";
}

/** A run's events as a byte stream of SSE frames. `send` makes it the
 *  stream of the run a new message starts; without it, of the run going
 *  now (or `agentRunId`). Rejects with `SocketUnavailable` when the socket
 *  cannot take the subscription in time. */
export async function socketRunStream(deps: RunStreamDeps, request: {
    conversationId: string;
    agentRunId?: string | null;
    send?: unknown;
    signal?: AbortSignal;
}): Promise<ReadableStream<Uint8Array>> {
    if (!socketCarries(deps.live.status)) throw new SocketUnavailable("The live socket is down.");
    const later = deps.setTimeout ?? ((run: () => void, ms: number) => setTimeout(run, ms));
    const cancelTimer = deps.clearTimeout ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
    const encoder = new TextEncoder();
    let target: string | null = request.agentRunId ?? null;
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
    let done = false;
    let subscription: LiveSubscription | null = null;
    let unwatch: (() => void) | null = null;
    const backlog: string[] = [];

    const finish = () => {
        if (done) return;
        done = true;
        unwatch?.();
        subscription?.close();
        try {
            controller?.close();
        } catch {
            /* already closed by its reader */
        }
    };
    const emit = (frame: string) => {
        if (done) return;
        if (controller) controller.enqueue(encoder.encode(frame));
        else backlog.push(frame);
    };
    let subscribed!: () => void;
    let refused!: (error: Error) => void;
    const ready = new Promise<void>((resolve, reject) => { subscribed = resolve; refused = reject; });
    const wait = later(() => refused(new SocketUnavailable("The live socket did not take the subscription in time.")), deps.subscribeMs ?? 4_000);

    const sending = request.send !== undefined;
    /* A send's own events can arrive before the send is answered; they wait,
       so the stream says first what the send did, as the SSE route does. */
    const early: Record<string, unknown>[] = [];
    const forward = (event: Record<string, unknown>) => {
        if (done) return;
        const run = typeof event.agent_run_id === "string" ? event.agent_run_id : null;
        if (run && target && run !== target) return;
        if (run && !target) target = run;
        emit(sseFrame(event));
        if (TERMINAL.has(String(event.type))) finish();
    };
    subscription = deps.live.subscribe("conversation", {
        conversation_id: request.conversationId,
        ...(request.agentRunId ? { agent_run_id: request.agentRunId } : {}),
    }, {
        onSubscribed: () => subscribed(),
        onEvents: (events) => {
            for (const raw of events) {
                if (done || !raw || typeof raw !== "object") continue;
                const event = raw as Record<string, unknown>;
                const run = typeof event.agent_run_id === "string" ? event.agent_run_id : null;
                if (event.type === "run") {
                    /* Which run the socket watches. Attaching to nothing ends
                       the stream, as the SSE route closes; a send names its
                       own run when it is answered. */
                    if (!sending && !run) finish();
                    else if (!sending && !target) target = run;
                    continue;
                }
                if (sending && !target) early.push(event);
                else forward(event);
            }
        },
        /* Events were lost: end here, and the session reads and attaches again. */
        onResync: () => finish(),
        onEnded: (reason) => {
            refused(new SocketUnavailable("The live socket refused the conversation: " + reason));
            finish();
        },
    });

    try {
        await ready;
    } catch (error) {
        subscription.close();
        throw error;
    } finally {
        cancelTimer(wait);
    }

    const stream = new ReadableStream<Uint8Array>({
        start(given) {
            controller = given;
            for (const frame of backlog.splice(0)) given.enqueue(encoder.encode(frame));
            if (done) given.close();
        },
        cancel() { finish(); },
    });
    request.signal?.addEventListener("abort", finish, { once: true });
    unwatch = deps.live.onStatus((status) => { if (status !== "open") finish(); });

    if (sending) {
        try {
            const start = await deps.append(request.conversationId, request.send);
            target = start.agent_run_id;
            /* The SSE route's first frame: whether this send started a run or
               joined the one already working. */
            emit(sseFrame({ type: "status", data: { started_new_run: start.started_new_run ?? true, deduplicated: start.deduplicated ?? false }, agent_run_id: start.agent_run_id }));
            for (const event of early.splice(0)) forward(event);
            /* A re-send joins the run its first copy started, which may have
               ended -- and said so -- before this subscription listened. */
            if (start.deduplicated && !isRunning(await deps.status(request.conversationId))) finish();
        } catch (error) {
            finish();
            throw error;
        }
    }
    return stream;
}
