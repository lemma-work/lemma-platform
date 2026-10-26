/** The call's one socket to the backend.
 *
 *  Microphone audio goes up as binary frames (16 kHz, 16-bit mono PCM) and the
 *  voice comes down the same way at the rate `ready` names; everything else is
 *  JSON. Which voice engine talks, and every agent behind it, is the server's:
 *  nothing here holds a provider's key or knows which provider answered. */

export interface CanvasFrameItem {
    conversation_id: string;
    tool_call_id: string;
    tool_name: string;
    args: unknown;
    result: unknown;
}

export type CallFrame =
    | { type: "ready"; call_id: string; engine: string; sample_rate: number }
    | { type: "transcript"; speaker: "caller" | "voice"; text: string; final: boolean }
    | { type: "clear" }
    | { type: "work"; running: boolean }
    /** Something the person should know that the voice is also saying: a
     *  request the team cannot take, and why. The call stays up. */
    | { type: "notice"; text: string }
    | { type: "canvas.item"; item: CanvasFrameItem; replay?: boolean }
    | { type: "ended"; reason: string | null };

export interface CallStart {
    teammate: string;
    pod: string;
    /** What came before the call, for the voice's brief. */
    context: string;
    engine?: string;
    /** Whether the microphone is muted, so a rejoin keeps it muted. */
    muted?: boolean;
}

export interface CallSocketHandlers {
    onFrame(frame: CallFrame): void;
    onAudio(pcm: ArrayBuffer): void;
    /** The socket closed: `reason` is a sentence for the person, or null for an
     *  ordinary end. `dropped` is a close neither side chose -- the network,
     *  a server restart -- after which the same call can be joined again. */
    onClose(reason: string | null, dropped: boolean): void;
}

export function callSocketUrl(apiUrl: string, podId: string, callId: string, token: string | null): string {
    const root = apiUrl.replace(/\/$/, "").replace(/^http(s?):\/\//, "ws$1://");
    const url = new URL(`${root}/pods/${encodeURIComponent(podId)}/calls/${encodeURIComponent(callId)}/socket`);
    if (token) url.searchParams.set("access_token", token);
    return url.toString();
}

/** How long ending a call waits for the server to save it and close. */
const END_TIMEOUT_MS = 4000;

/** Close codes the server ends a call with, as the person should read them. */
const CLOSE_REASONS: Record<number, string> = {
    4401: "Your session expired. Sign in again to call.",
    4403: "You can no longer reach this pod.",
    4404: "This call no longer exists.",
    4429: "You already have the most calls open at once.",
    4503: "The voice is not available right now.",
};

/** What a close means: an ordinary end (no reason), the server refusing or
 *  ending the call on purpose (its sentence, not to be retried), or a drop
 *  nobody chose, after which the same call can be joined again. */
export function closeOutcome(code: number, reason = ""): { reason: string | null; dropped: boolean } {
    if (code === 1000 || code === 1005) return { reason: null, dropped: false };
    const refused = CLOSE_REASONS[code];
    if (refused) return { reason: refused, dropped: false };
    return { reason: reason || "The call dropped.", dropped: true };
}

export function openCallSocket(url: string, start: CallStart, handlers: CallSocketHandlers): CallConnection {
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    let ended = false;
    const end = (reason: string | null, dropped = false) => {
        if (ended) return;
        ended = true;
        handlers.onClose(reason, dropped);
    };
    let opened = false;
    socket.onopen = () => {
        opened = true;
        socket.send(JSON.stringify({ type: "start", ...start }));
    };
    socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
            handlers.onAudio(event.data);
            return;
        }
        let frame: unknown;
        try { frame = JSON.parse(String(event.data)); } catch { return; }
        if (!frame || typeof frame !== "object" || typeof (frame as { type?: unknown }).type !== "string") return;
        const typed = frame as CallFrame;
        if (typed.type === "ended") end(typed.reason);
        else handlers.onFrame(typed);
    };
    socket.onclose = (event) => {
        /* Never opened: the server or the network is not there yet -- often
           a rejoin started right after a drop. Worth another try, which the
           caller bounds. */
        if (!opened) { end("The call could not connect.", true); return; }
        const outcome = closeOutcome(event.code, event.reason);
        end(outcome.reason, outcome.dropped);
    };
    /* No verdict here. An unclean drop fires `error` and then `close`; ending
       on `error` latched "could not connect" and the close that says it was a
       drop -- one the call can rejoin -- was never heard. A socket that never
       opened closes too, as 1006, and is told apart there. */
    socket.onerror = () => undefined;
    return new CallConnection(socket);
}

export class CallConnection {
    private readonly socket: WebSocket;

    constructor(socket: WebSocket) {
        this.socket = socket;
    }

    private open(): boolean {
        return this.socket.readyState === WebSocket.OPEN;
    }

    sendAudio(pcm: Int16Array): void {
        // Behind on sending means the network cannot keep up; late microphone
        // audio is worse than missing audio, so it is dropped rather than queued.
        if (this.open() && this.socket.bufferedAmount < 64 * 1024) this.socket.send(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength));
    }

    send(control: Record<string, unknown>): void {
        if (this.open()) this.socket.send(JSON.stringify(control));
    }

    /** Ask the server to end the call. Resolves true once the server has
     *  closed it, which it does only after saving the call's transcript, so
     *  whoever reloads the conversation on that sees it; false when the
     *  server did not answer in time and this side closed instead. */
    close(): Promise<boolean> {
        const socket = this.socket;
        return new Promise((resolve) => {
            if (socket.readyState === WebSocket.CLOSED) { resolve(false); return; }
            // Normal closes only: an error close is not the server saying it saved.
            const done = (event: CloseEvent) => {
                window.clearTimeout(timer);
                resolve(event.code === 1000 || event.code === 1005);
            };
            const timer = window.setTimeout(() => {
                socket.removeEventListener("close", done);
                socket.close(1000);
                resolve(false);
            }, END_TIMEOUT_MS);
            socket.addEventListener("close", done, { once: true });
            if (this.open()) this.send({ type: "end" });
            else socket.close(1000);
        });
    }
}
